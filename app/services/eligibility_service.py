"""Bridges stored schemes to the pure eligibility engine.

Two loaders produce identical ``SchemeRules`` (same ``rules_hash``): one from a validated data
file, one from the database. A test asserts they agree, so what the validator accepted is what
the engine evaluates.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import models as m
from app.domain.eligibility import (
    Assessment, Group, OtherCondition, Rule, SchemeRules, Tri, evaluate_group, evaluate_scheme,
)
from app.domain.scheme_schema import GroupSpec, RuleSpec, SchemeFile


class SchemeNotFound(LookupError):
    pass


def _d(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


# ----------------------------------------------------------------------------- from a data file
def from_scheme_file(scheme: SchemeFile) -> SchemeRules:
    urls = {s.key: (s.url, s.title) for s in scheme.sources}
    verified = scheme.verification_status != "draft"

    def rule(spec: RuleSpec) -> Rule:
        url, title = urls[spec.source]
        return Rule(spec.field, spec.op, spec.value, spec.as_of, spec.evaluability, verified, url, title, spec.locator)

    def group(spec: GroupSpec) -> Group:
        return Group(spec.logic, tuple(rule(i.rule) if i.rule else group(i.group) for i in spec.items),  # type: ignore[arg-type]
                     spec.description)

    exclusions = (Group("NOT_ANY", tuple(rule(r) for r in scheme.exclusions), "Exclusion criteria")
                  if scheme.exclusions else None)
    others = tuple(OtherCondition(c.kind, dict(c.text), urls[c.source][0], c.locator) for c in scheme.other_conditions)
    return SchemeRules(
        scheme.scheme_id, group(scheme.eligibility) if scheme.eligibility else None, exclusions,
        scheme.rules_complete, scheme.status, scheme.verification_status, scheme.volatility,
        scheme.last_verified, scheme.next_review_due, scheme.application_deadline, others)


# ----------------------------------------------------------------------------- from the database
def _rule_from_row(row: m.Rule, doc: m.SourceDocument | None) -> Rule:
    return Rule(row.field_key, row.operator, json.loads(row.value), _d(row.as_of_date), row.evaluability,
                row.rule_status == "verified", doc.url if doc else None, doc.title if doc else None,
                row.source_locator, row.rule_id)


def _load_group(session: Session, group_id: int) -> Group:
    row = session.get(m.RuleGroup, group_id)
    items: list[Rule | Group] = []
    for r in session.scalars(select(m.Rule).where(m.Rule.group_id == group_id).order_by(m.Rule.rule_id)):
        items.append(_rule_from_row(r, session.get(m.SourceDocument, r.source_doc_id)))
    for child in session.scalars(select(m.RuleGroup.group_id).where(m.RuleGroup.parent_group_id == group_id)
                                 .order_by(m.RuleGroup.group_id)):
        items.append(_load_group(session, child))
    return Group(row.logic, tuple(items), row.description)


def load_scheme_rules(session: Session, scheme_id: str) -> SchemeRules:
    s = session.get(m.Scheme, scheme_id)
    if s is None:
        raise SchemeNotFound(scheme_id)
    top = {p: None for p in ("eligibility", "exclusion")}
    for purpose in top:
        gid = session.scalar(select(m.RuleGroup.group_id).where(
            m.RuleGroup.scheme_id == scheme_id, m.RuleGroup.purpose == purpose,
            m.RuleGroup.parent_group_id.is_(None)).order_by(m.RuleGroup.group_id))
        top[purpose] = _load_group(session, gid) if gid is not None else None
    others = tuple(
        OtherCondition(c.kind, json.loads(c.texts), doc.url if doc else None, c.source_locator)
        for c in session.scalars(select(m.SchemeCondition).where(m.SchemeCondition.scheme_id == scheme_id)
                                 .order_by(m.SchemeCondition.position))
        for doc in [session.get(m.SourceDocument, c.source_doc_id)])
    return SchemeRules(
        scheme_id, top["eligibility"], top["exclusion"], bool(s.rules_complete), s.status, s.verification_status,
        s.volatility, _d(s.last_verified), _d(s.next_review_due), _d(s.application_deadline), others)


def load_condition_group(session: Session, group_id: int) -> Group:
    return _load_group(session, group_id)


# ----------------------------------------------------------------------------- evaluation
def evaluate_from_db(session: Session, scheme_id: str, profile: Mapping[str, Any], *, today: date) -> Assessment:
    return evaluate_scheme(load_scheme_rules(session, scheme_id), profile, today=today)


def condition_applies(session: Session, group_id: int, profile: Mapping[str, Any], *, today: date) -> Tri:
    """Whether a conditional document's condition holds: PASS/FAIL/UNKNOWN/OFFICIAL_ONLY."""
    result, _ = evaluate_group(load_condition_group(session, group_id), profile, today=today)
    return result
