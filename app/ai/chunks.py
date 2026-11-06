"""Turn the verified scheme records into small, citable chunks.

Every chunk carries the scheme, section, language and the official source (url, title, locator) it came from, so
anything we later show can be traced back. Only text that really exists in a language is indexed in that language:
English text shown as a fallback in a Hindi page is not duplicated into the Hindi index.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import models as m
from app.services.catalog_service import get_scheme_detail

LANGS = ("en", "hi", "mr")
MAX_CHARS = 1200                      # roughly 250-300 tokens: small enough to keep one idea per chunk
SECTIONS = ("overview", "benefits", "who_for", "eligibility", "condition", "documents", "steps")
DOC_KINDS = ("mandatory", "conditional", "optional")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    scheme_id: str
    scheme_name: str
    section: str
    language: str
    text: str                                  # what is embedded and searched (prefixed with scheme and section)
    body: str                                  # the text without the prefix, for showing to a person
    sources: tuple[tuple[str, str], ...] = field(default_factory=tuple)   # (url, title)
    locator: str | None = None
    authority_tier: int | None = None
    verification_status: str = "draft"
    last_verified: str | None = None
    state_code: str | None = None
    scheme_status: str = "unknown"
    kind: str | None = None                    # condition kind, or document requirement type


def make_id(scheme_id: str, lang: str, section: str, n: int, body: str) -> str:
    return hashlib.sha1(f"{scheme_id}|{lang}|{section}|{n}|{body}".encode("utf-8")).hexdigest()[:16]


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split on sentence ends so a rule is never cut in half; a single very long sentence is kept whole."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?।])\s+", text):
        if cur and len(cur) + 1 + len(sent) > limit:
            parts.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return parts


def _rule_lines(node: dict[str, Any] | None) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """(sentences, locators, sources) for the checkable eligibility tree."""
    if not node:
        return [], [], []
    if node["type"] == "rule":
        src = [(node["source_url"], node["source_url"])] if node.get("source_url") else []
        return [f"{node['label']}: {node['requirement']}"], [node["locator"]] if node.get("locator") else [], src
    lines: list[str] = []
    locs: list[str] = []
    srcs: list[tuple[str, str]] = []
    for item in node["items"]:
        a, b, c = _rule_lines(item)
        lines += a
        locs += b
        srcs += c
    return lines, locs, srcs


def _uniq(seq: list) -> list:
    return list(dict.fromkeys(seq))


def chunks_for_scheme(detail: dict[str, Any], lang: str) -> list[Chunk]:
    tr = detail.get("translation") or {}
    name = tr.get("name") or detail["scheme_id"]
    rec = detail["record"]
    base = dict(scheme_id=detail["scheme_id"], scheme_name=name, language=lang,
                verification_status=rec["verification_status"], last_verified=rec["last_verified"],
                state_code=detail.get("state_code"), scheme_status=rec["status"])
    all_sources = _uniq([(r["source"]["url"], r["source"]["title"]) for key in ("other_conditions", "documents", "steps")
                         for r in detail.get(key, []) if r.get("source")])
    titles = dict(all_sources)
    out: list[Chunk] = []
    counter: dict[str, int] = {}

    def add(section: str, body: str, sources, locator=None, tier=None, kind=None) -> None:
        for piece in split_text(body):
            n = counter[section] = counter.get(section, -1) + 1
            out.append(Chunk(chunk_id=make_id(detail["scheme_id"], lang, section, n, piece), section=section,
                             text=f"{name} | {section}: {piece}", body=piece, sources=tuple(sources),
                             locator=locator, authority_tier=tier, kind=kind, **base))

    if tr and tr.get("language_used") == lang:
        for section, key in (("overview", "short_description"), ("benefits", "benefits"), ("who_for", "target_beneficiaries")):
            if tr.get(key):
                add(section, tr[key], all_sources, locator="curated summary of the official sources listed")
    lines, locs, srcs = _rule_lines(detail.get("eligibility"))
    if lines:                                              # tree text is rendered in the requested language
        add("eligibility", " ".join(f"{x}." if not x.endswith(".") else x for x in lines),
            [(u, titles.get(u, u)) for u, _ in _uniq(srcs)] or all_sources,
            locator="; ".join(_uniq(locs)) or None)
    for c in detail.get("other_conditions", []):
        if c.get("language_used") == lang:
            s = c["source"]
            add("condition", c["text"], [(s["url"], s["title"])], locator=c["locator"], tier=s.get("authority_tier"), kind=c["kind"])
    for kind in DOC_KINDS:
        rows = [d for d in detail.get("documents", []) if d["requirement_type"] == kind and d.get("language_used", lang) == lang]
        if rows:
            body = "; ".join(d["name"] + (f" ({d['purpose_note']})" if d.get("purpose_note") else "") for d in rows) + "."
            srcs2 = _uniq([(d["source"]["url"], d["source"]["title"]) for d in rows if d.get("source")])
            add("documents", body, srcs2 or all_sources, locator="; ".join(_uniq([d["locator"] for d in rows])), kind=kind,
                tier=min((d["source"].get("authority_tier") or 9 for d in rows if d.get("source")), default=None))
    steps = [s for s in detail.get("steps", []) if s.get("language_used", lang) == lang]
    for i in range(0, len(steps), 4):
        grp = steps[i:i + 4]
        add("steps", " ".join(f"{s['number']}. {s['instruction']}" for s in grp),
            _uniq([(s["source"]["url"], s["source"]["title"]) for s in grp if s.get("source")]) or all_sources,
            locator="; ".join(_uniq([s["locator"] for s in grp])))
    return out


def build_chunks(session: Session, today: date, langs: tuple[str, ...] = LANGS) -> list[Chunk]:
    """All chunks for every scheme record whose details we hold. Draft records are included but keep their label,
    so the answering layer can refuse to present them as verified."""
    out: list[Chunk] = []
    for sid in session.scalars(select(m.Scheme.scheme_id).order_by(m.Scheme.scheme_id)):
        for lang in langs:
            out += chunks_for_scheme(get_scheme_detail(session, sid, lang=lang, today=today), lang)
    return out
