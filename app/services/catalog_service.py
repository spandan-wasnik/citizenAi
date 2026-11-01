"""Read-side queries for the API: scheme list, search and detail. Returns plain dicts; no I/O besides the session."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import models as m
from app.domain.eligibility import Group, Rule
from app.domain.explain import LABELS, PRELIMINARY_NOTICE, describe_requirement
from app.services.eligibility_service import SchemeNotFound, load_scheme_rules

SUPPORTED_LANGS = ("en", "hi", "mr")
LINK_BADGE = {
    "verified_manual": "Verified by a person",
    "auto_checked": "Link responded when checked (this is not a verification)",
    "unverified": "Not verified",
    "broken": "Link appears broken",
    "redirected_unverified": "Redirects elsewhere; not verified",
    "suspicious": "Flagged as suspicious; do not use",
}


def _name_map(session: Session, lang: str) -> dict[str, tuple[str, str]]:
    """scheme_id -> (name, language actually used), preferring `lang`, falling back to English."""
    rows = session.execute(select(m.SchemeTranslation.scheme_id, m.SchemeTranslation.lang,
                                  m.SchemeTranslation.official_name)).all()
    best: dict[str, tuple[str, str]] = {}
    for sid, tl, name in rows:
        if tl == "en" and sid not in best:
            best[sid] = (name, "en")
        if tl == lang:
            best[sid] = (name, lang)
    return best


def list_schemes(session: Session, *, lang: str = "en", level: str | None = None, state: str | None = None,
                 verification_status: str | None = None, tag: str | None = None,
                 q: str | None = None) -> list[dict[str, Any]]:
    query = select(m.Scheme).order_by(m.Scheme.scheme_id)
    if level:
        query = query.where(m.Scheme.level == level)
    if state:  # central schemes apply everywhere, so they stay in the list
        query = query.where(or_(m.Scheme.state_code == state, m.Scheme.state_code.is_(None)))
    if verification_status:
        query = query.where(m.Scheme.verification_status == verification_status)
    if tag:
        query = query.where(m.Scheme.scheme_id.in_(select(m.SchemeTag.scheme_id).where(m.SchemeTag.tag == tag)))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(
            m.Scheme.scheme_id.like(like),
            m.Scheme.scheme_id.in_(select(m.SchemeTranslation.scheme_id).where(or_(
                m.SchemeTranslation.official_name.like(like), m.SchemeTranslation.short_description.like(like),
                m.SchemeTranslation.benefits.like(like), m.SchemeTranslation.target_beneficiaries.like(like)))),
            m.Scheme.scheme_id.in_(select(m.SchemeTag.scheme_id).where(m.SchemeTag.tag.like(like)))))
    names = _name_map(session, lang)
    out = []
    for s in session.scalars(query):
        name, used = names.get(s.scheme_id, (s.scheme_id, "en"))
        out.append({"scheme_id": s.scheme_id, "name": name, "language_used": used, "level": s.level,
                    "state_code": s.state_code, "status": s.status, "verification_status": s.verification_status,
                    "last_verified": s.last_verified.isoformat() if s.last_verified else None})
    return out


def _source_info(session: Session, doc_id: int, cache: dict[int, dict]) -> dict:
    if doc_id not in cache:
        doc = session.get(m.SourceDocument, doc_id)
        src = session.get(m.Source, doc.source_id)
        cache[doc_id] = {"url": doc.url, "title": doc.title, "doc_type": doc.doc_type,
                         "retrieved_at": doc.retrieved_at, "publisher": src.organization,
                         "domain": src.domain, "authority_tier": src.authority_tier}
    return cache[doc_id]


def _tree(group: Group | Rule, lang: str) -> dict:
    if isinstance(group, Rule):
        return {"type": "rule", "field": group.field, "label": LABELS[lang].get(group.field, group.field),
                "requirement": describe_requirement(group, lang), "as_of": group.as_of.isoformat() if group.as_of else None,
                "checkable_from_profile": group.evaluability == "auto" and group.verified,
                "source_url": group.source_url, "locator": group.locator}
    return {"type": "group", "logic": group.logic, "description": group.description,
            "items": [_tree(i, lang) for i in group.items]}


def get_scheme_detail(session: Session, scheme_id: str, *, lang: str = "en", today: date) -> dict[str, Any]:
    s = session.get(m.Scheme, scheme_id)
    if s is None:
        raise SchemeNotFound(scheme_id)
    cache: dict[int, dict] = {}
    trs = {t.lang: t for t in session.scalars(select(m.SchemeTranslation).where(m.SchemeTranslation.scheme_id == scheme_id))}
    tr = trs.get(lang) or trs.get("en")
    translation = None if tr is None else {
        "language_used": tr.lang, "name": tr.official_name, "short_description": tr.short_description,
        "benefits": tr.benefits, "target_beneficiaries": tr.target_beneficiaries, "origin": tr.origin}

    rules = load_scheme_rules(session, scheme_id)
    doc_names = {(t.document_code, t.lang): t.name for t in session.scalars(select(m.DocumentTranslation))}
    documents = []
    for d in session.scalars(select(m.SchemeDocument).where(m.SchemeDocument.scheme_id == scheme_id)
                             .order_by(m.SchemeDocument.requirement_type, m.SchemeDocument.document_code)):
        documents.append({"code": d.document_code, "name": doc_names.get((d.document_code, lang))
                          or doc_names.get((d.document_code, "en")) or d.document_code,
                          "requirement_type": d.requirement_type, "purpose_note": d.purpose_note,
                          "source": _source_info(session, d.source_doc_id, cache), "locator": d.source_locator})

    links = session.scalars(select(m.Link).where(m.Link.scheme_id == scheme_id).order_by(m.Link.link_id)).all()
    link_url = {l.link_id: l.url for l in links}
    steps_rows = session.scalars(select(m.ApplicationStep).where(m.ApplicationStep.scheme_id == scheme_id)
                                 .order_by(m.ApplicationStep.lang, m.ApplicationStep.step_number)).all()
    step_lang = lang if any(r.lang == lang for r in steps_rows) else "en"
    steps = [{"number": r.step_number, "instruction": r.instruction, "link": link_url.get(r.link_id),
              "source": _source_info(session, r.source_doc_id, cache), "locator": r.source_locator}
             for r in steps_rows if r.lang == step_lang]

    conditions = [{"kind": c.kind, "text": (json.loads(c.texts).get(lang) or json.loads(c.texts).get("en")),
                   "language_used": lang if lang in json.loads(c.texts) else "en",
                   "source": _source_info(session, c.source_doc_id, cache), "locator": c.source_locator}
                  for c in session.scalars(select(m.SchemeCondition).where(m.SchemeCondition.scheme_id == scheme_id)
                                           .order_by(m.SchemeCondition.position))]

    return {
        "scheme_id": s.scheme_id, "level": s.level, "state_code": s.state_code, "ministry": s.ministry,
        "department": s.department, "translation": translation,
        "record": {"status": s.status, "verification_status": s.verification_status,
                   "rules_complete": bool(s.rules_complete), "volatility": s.volatility,
                   "last_verified": s.last_verified.isoformat() if s.last_verified else None,
                   "next_review_due": s.next_review_due.isoformat() if s.next_review_due else None,
                   "application_deadline": s.application_deadline.isoformat() if s.application_deadline else None,
                   "is_stale": bool(s.next_review_due and today > s.next_review_due)},
        "eligibility": _tree(rules.eligibility, "en") if rules.eligibility else None,
        "exclusions": [_tree(r, "en") for r in (rules.exclusions.items if rules.exclusions else ())],
        "other_conditions": conditions, "documents": documents, "steps": steps,
        "links": [{"url": l.url, "link_type": l.link_type, "domain_class": l.domain_class,
                   "verification_status": l.verification_status, "badge": LINK_BADGE.get(l.verification_status, ""),
                   "last_checked_at": l.last_checked_at, "notes": l.notes, "open_in_new_tab": True} for l in links],
        "notice": PRELIMINARY_NOTICE["en"],
    }
