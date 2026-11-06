"""Extractive question answering: the answer is the recorded text itself, with its source. No model writes anything.

When no chunk is real evidence for the question, we say so instead of answering from the nearest text.
The confidence label comes from retrieval and source quality, never from a model's own opinion of itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.retrieval import Retrieval, Retriever

MAX_EVIDENCE = 3


@dataclass
class Answer:
    found: bool
    passages: list[dict[str, Any]] = field(default_factory=list)   # verbatim recorded text, each with its source
    sources: list[dict[str, Any]] = field(default_factory=list)    # distinct, ordered
    confidence: str = "none"                                       # high | medium | low | none
    requires_verification: bool = True
    scheme_ids: list[str] = field(default_factory=list)
    mode: str = "keyword_only"
    note: str | None = None


def confidence_label(r: Retrieval) -> str:
    ev = [h for h in r.hits if h.evidence]
    if not ev:
        return "none"
    best = ev[0]
    good_source = best.chunk.authority_tier in (1, None) and best.chunk.verification_status != "draft"
    if best.coverage >= 0.67 and good_source and (len(ev) >= 2 or r.mode == "hybrid"):
        return "high"
    if best.coverage >= 0.5 and good_source:
        return "medium"
    return "low"


def answer(retriever: Retriever, question: str, *, language: str, scheme_ids: tuple[str, ...] | None = None,
           intent: str | None = None) -> Answer:
    r = retriever.search(question, language=language, scheme_ids=scheme_ids, intent=intent)
    ev = [h for h in r.hits if h.evidence][:MAX_EVIDENCE]
    if not ev:
        return Answer(found=False, mode=r.mode, note=r.note, scheme_ids=list(scheme_ids or []))
    passages, sources, seen = [], [], set()
    for h in ev:
        c = h.chunk
        passages.append({"scheme_id": c.scheme_id, "scheme_name": c.scheme_name, "section": c.section, "text": c.body,
                         "language": c.language, "locator": c.locator, "sources": [{"url": u, "title": t} for u, t in c.sources],
                         "verification_status": c.verification_status, "last_verified": c.last_verified})
        for u, t in c.sources:
            if u not in seen:
                seen.add(u)
                sources.append({"url": u, "title": t})
    ok = all(h.chunk.verification_status == "human_reviewed" and h.chunk.scheme_status == "active" for h in ev)
    return Answer(found=True, passages=passages, sources=sources, confidence=confidence_label(r), requires_verification=not ok,
                  scheme_ids=list(dict.fromkeys(p["scheme_id"] for p in passages)), mode=r.mode, note=r.note)
