"""Deterministic, three-valued eligibility engine. Pure Python: no I/O, no models, no clock.

Principles (each is covered by tests):
  * A missing or unusable answer is UNKNOWN. It is never treated as a pass.
  * Combination is Kleene logic, so a result is certain only if no answer to an unanswered
    question could change it (soundness is property-tested).
  * A negative verdict (LIKELY_INELIGIBLE) needs a verified, complete, fresh record; otherwise
    the engine says REQUIRES_OFFICIAL_VERIFICATION. Draft records never produce a verdict.
  * Age rules with an as_of date are evaluated over every age the person could have had on that
    date (the birthday is unknown), so "18 on 1 July" is not guessed from today's age.
  * The profile must contain only facts the user stated or corrected. Inferred values must be
    confirmed before they reach this module (the caller's job, see the conversation workflow).
Meaning of results: a leaf in an *exclusion* group is the excluding condition, so PASS there
means "this exclusion applies to you" (see RuleResult.purpose).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Iterable, Mapping

from app.domain.fields import FIELD_TYPES, PROFILE_FIELDS, allowed_values

ENGINE_VERSION = "1"
SENSITIVE_FIELDS = frozenset(key for key, _t, _e, sensitive, _d in PROFILE_FIELDS if sensitive)


class Tri(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    OFFICIAL = "OFFICIAL_ONLY"  # cannot be self-assessed; needs official verification


class Status(str, Enum):
    LIKELY_ELIGIBLE = "LIKELY_ELIGIBLE"
    LIKELY_INELIGIBLE = "LIKELY_INELIGIBLE"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
    REQUIRES_OFFICIAL_VERIFICATION = "REQUIRES_OFFICIAL_VERIFICATION"


# ----------------------------------------------------------------------------- rule model
@dataclass(frozen=True)
class Rule:
    field: str
    op: str
    value: Any = None
    as_of: date | None = None
    evaluability: str = "auto"
    verified: bool = True
    source_url: str | None = None
    source_title: str | None = None
    locator: str = ""
    rule_id: int | None = None


@dataclass(frozen=True)
class Group:
    logic: str  # AND | OR | NOT_ANY
    items: tuple[Rule | "Group", ...]
    description: str | None = None


@dataclass(frozen=True)
class OtherCondition:
    """An official condition no profile answer can settle. Never evaluated; shown as 'confirm this officially'."""
    kind: str  # exclusion | requirement
    texts: Mapping[str, str]
    source_url: str | None = None
    locator: str = ""


@dataclass(frozen=True)
class SchemeRules:
    scheme_id: str
    eligibility: Group | None = None
    exclusions: Group | None = None  # a NOT_ANY group of excluding conditions
    rules_complete: bool = False
    status: str = "unknown"
    verification_status: str = "draft"
    volatility: str = "stable"
    last_verified: date | None = None
    next_review_due: date | None = None
    application_deadline: date | None = None
    other_conditions: tuple[OtherCondition, ...] = ()


@dataclass(frozen=True)
class RuleResult:
    rule: Rule
    purpose: str  # eligibility | exclusion | conditional
    user_value: Any
    result: Tri
    reason_code: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MissingField:
    field: str
    reason: str  # missing_value | invalid_value | birth_date_needed
    sensitive: bool


@dataclass(frozen=True)
class Assessment:
    scheme_id: str
    status: Status
    top: Tri
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    results: tuple[RuleResult, ...]
    missing_fields: tuple[MissingField, ...]
    engine_version: str
    rules_hash: str
    evaluated_on: date
    other_conditions: tuple[OtherCondition, ...] = ()


# ----------------------------------------------------------------------------- hashing
def _canon_rule(r: Rule) -> dict:
    return {"f": r.field, "op": r.op, "v": r.value, "as_of": r.as_of.isoformat() if r.as_of else None,
            "ev": r.evaluability, "url": r.source_url, "loc": r.locator}


def _canon_group(g: Group) -> dict:
    items = [_canon_rule(i) if isinstance(i, Rule) else _canon_group(i) for i in g.items]
    items.sort(key=lambda x: json.dumps(x, sort_keys=True, default=str))  # order-insensitive
    return {"logic": g.logic, "items": items}


def rules_hash(scheme: SchemeRules) -> str:
    """Stable fingerprint of the rules (independent of item order and of where they were loaded from)."""
    blob = {"e": _canon_group(scheme.eligibility) if scheme.eligibility else None,
            "x": _canon_group(scheme.exclusions) if scheme.exclusions else None,
            "o": sorted(([c.kind, dict(c.texts), c.source_url, c.locator] for c in scheme.other_conditions),
                        key=lambda x: json.dumps(x, sort_keys=True, default=str))}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()


# ----------------------------------------------------------------------------- profile values
_INVALID = object()


def _normalise(field_key: str, raw: Any) -> Any:
    """Return a canonical value, None if absent, or _INVALID if present but unusable."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    vtype = FIELD_TYPES.get(field_key)
    if vtype in ("int", "money_inr"):
        if isinstance(raw, bool):
            return _INVALID
        if isinstance(raw, float) and raw.is_integer():
            raw = int(raw)
        return raw if isinstance(raw, int) and raw >= 0 else _INVALID
    if vtype == "bool":
        return raw if isinstance(raw, bool) else _INVALID
    if not isinstance(raw, str):
        return _INVALID
    text = raw.strip().casefold()
    if vtype == "enum":
        allowed = {a.casefold() for a in (allowed_values(field_key) or ())}
        return text if text in allowed else _INVALID
    return text  # free-text string field


def _fold(value: Any) -> Any:
    return value.strip().casefold() if isinstance(value, str) else value


# ----------------------------------------------------------------------------- leaf evaluation
def _predicate(op: str, x: Any, value: Any) -> bool:
    if op == "eq":
        return x == _fold(value)
    if op == "neq":
        return x != _fold(value)
    if op == "in":
        return x in [_fold(v) for v in value]
    if op == "not_in":
        return x not in [_fold(v) for v in value]
    if op == "gte":
        return x >= value
    if op == "lte":
        return x <= value
    if op == "gt":
        return x > value
    if op == "lt":
        return x < value
    if op == "between":
        return value[0] <= x <= value[1]
    if op == "is_true":
        return x is True
    if op == "is_false":
        return x is False
    raise ValueError(f"unknown operator {op!r}")


def _age_candidates(age_today: int, as_of: date, today: date) -> tuple[int, ...]:
    """Every whole-year age the person could have had on ``as_of`` (birthday unknown): at most two values."""
    if as_of == today:
        return (age_today,)
    if as_of > today:
        whole = as_of.year - today.year - (1 if (as_of.month, as_of.day) < (today.month, today.day) else 0)
        exact = (as_of.month, as_of.day) == (today.month, today.day)
        low = age_today + whole
    else:
        back = today.year - as_of.year - (1 if (today.month, today.day) < (as_of.month, as_of.day) else 0)
        exact = (as_of.month, as_of.day) == (today.month, today.day)
        low = age_today - back - (0 if exact else 1)
    return (low,) if exact else (low, low + 1)


def _eval_rule(rule: Rule, purpose: str, profile: Mapping[str, Any], today: date) -> RuleResult:
    raw = profile.get(rule.field)

    def res(result: Tri, code: str, **params: Any) -> RuleResult:
        return RuleResult(rule, purpose, raw, result, code, params)

    if not rule.verified:
        return res(Tri.OFFICIAL, "rule_not_verified")
    if rule.evaluability == "official_only":
        return res(Tri.OFFICIAL, "official_only")
    value = _normalise(rule.field, raw)
    if value is None:
        return res(Tri.UNKNOWN, "missing_value")
    if value is _INVALID:
        return res(Tri.UNKNOWN, "invalid_value")

    if rule.field == "age_years" and rule.as_of is not None:
        candidates = _age_candidates(value, rule.as_of, today)
        outcomes = {_predicate(rule.op, c, rule.value) for c in candidates}
        if len(outcomes) > 1:
            return res(Tri.UNKNOWN, "age_depends_on_birthday", as_of=rule.as_of.isoformat(),
                       possible_ages=list(candidates))
        passed = outcomes.pop()
        return res(Tri.PASS if passed else Tri.FAIL, "meets_requirement" if passed else "fails_requirement",
                   as_of=rule.as_of.isoformat(), possible_ages=list(candidates))

    passed = _predicate(rule.op, value, rule.value)
    extra = {"as_of": rule.as_of.isoformat()} if rule.as_of else {}
    return res(Tri.PASS if passed else Tri.FAIL, "meets_requirement" if passed else "fails_requirement", **extra)


# ----------------------------------------------------------------------------- combination
def combine(logic: str, results: Iterable[Tri]) -> Tri:
    rs = list(results)
    if not rs:
        raise ValueError("cannot combine an empty group")
    has = lambda t: any(r is t for r in rs)  # noqa: E731
    if logic == "AND":
        for t in (Tri.FAIL, Tri.UNKNOWN, Tri.OFFICIAL):
            if has(t):
                return t
        return Tri.PASS
    if logic == "OR":
        if has(Tri.PASS):
            return Tri.PASS
        for t in (Tri.UNKNOWN, Tri.OFFICIAL):
            if has(t):
                return t
        return Tri.FAIL
    if logic == "NOT_ANY":
        if has(Tri.PASS):
            return Tri.FAIL  # an exclusion applies
        for t in (Tri.UNKNOWN, Tri.OFFICIAL):
            if has(t):
                return t
        return Tri.PASS
    raise ValueError(f"unknown logic {logic!r}")


def _walk(group: Group, purpose: str, profile: Mapping[str, Any], today: date,
          cache: dict[int, RuleResult], out: list[RuleResult]) -> None:
    for item in group.items:
        if isinstance(item, Group):
            _walk(item, purpose, profile, today, cache, out)
        else:
            r = _eval_rule(item, purpose, profile, today)
            cache[id(item)] = r
            out.append(r)


def _fold_group(group: Group, cache: dict[int, RuleResult], overrides: Mapping[str, Tri]) -> Tri:
    parts = []
    for item in group.items:
        if isinstance(item, Group):
            parts.append(_fold_group(item, cache, overrides))
        else:
            r = cache[id(item)]
            parts.append(overrides.get(item.field, r.result) if r.result is Tri.UNKNOWN else r.result)
    return combine(group.logic, parts)


def evaluate_group(group: Group, profile: Mapping[str, Any], *, today: date,
                   purpose: str = "conditional") -> tuple[Tri, list[RuleResult]]:
    """Evaluate one group on its own (used for conditional documents)."""
    cache: dict[int, RuleResult] = {}
    out: list[RuleResult] = []
    _walk(group, purpose, profile, today, cache, out)
    return _fold_group(group, cache, {}), out


# ----------------------------------------------------------------------------- scheme level
def evaluate_scheme(scheme: SchemeRules, profile: Mapping[str, Any], *, today: date) -> Assessment:
    cache: dict[int, RuleResult] = {}
    results: list[RuleResult] = []
    groups: list[Group] = []
    if scheme.eligibility:
        _walk(scheme.eligibility, "eligibility", profile, today, cache, results)
        groups.append(scheme.eligibility)
    if scheme.exclusions:
        _walk(scheme.exclusions, "exclusion", profile, today, cache, results)
        groups.append(scheme.exclusions)

    def top_with(overrides: Mapping[str, Tri]) -> Tri:
        return combine("AND", [_fold_group(g, cache, overrides) for g in groups]) if groups else Tri.OFFICIAL

    top = top_with({})
    missing = _decisive_missing(results, top_with)

    stale = scheme.next_review_due is not None and today > scheme.next_review_due
    warnings: list[str] = []
    if stale:
        warnings.append("record_stale")
    if scheme.application_deadline is not None and today > scheme.application_deadline:
        warnings.append("deadline_passed")
    verified = scheme.verification_status in ("source_verified", "human_reviewed")
    trusted = verified and scheme.rules_complete and not stale

    reasons: list[str] = []
    if scheme.verification_status == "draft":
        status, reasons = Status.REQUIRES_OFFICIAL_VERIFICATION, ["record_is_draft"]
    elif not results:
        status, reasons = Status.REQUIRES_OFFICIAL_VERIFICATION, ["no_rules"]
    elif top is Tri.FAIL:
        if trusted:
            status = Status.LIKELY_INELIGIBLE
        else:
            status = Status.REQUIRES_OFFICIAL_VERIFICATION
            reasons = [r for r, bad in (("rules_incomplete", not scheme.rules_complete), ("record_stale", stale)) if bad]
    elif top is Tri.UNKNOWN:
        status = Status.INSUFFICIENT_INFORMATION
    elif top is Tri.OFFICIAL:
        status, reasons = Status.REQUIRES_OFFICIAL_VERIFICATION, ["official_only_rules"]
    else:
        if not scheme.rules_complete:
            reasons.append("rules_incomplete")
        if stale:
            reasons.append("record_stale")
        if scheme.status != "active":
            reasons.append(f"scheme_status_{scheme.status}")
        status = Status.REQUIRES_OFFICIAL_VERIFICATION if reasons else Status.LIKELY_ELIGIBLE

    if scheme.other_conditions:
        warnings.append("confirm_other_conditions")  # shown whatever the status: these are never checked by the engine
    return Assessment(scheme.scheme_id, status, top, tuple(reasons), tuple(warnings), tuple(results),
                      tuple(missing), ENGINE_VERSION, rules_hash(scheme), today, scheme.other_conditions)


def _decisive_missing(results: list[RuleResult], top_with) -> list[MissingField]:
    """Fields whose answer could change the outcome (assume PASS vs FAIL for that field and compare)."""
    reasons: dict[str, str] = {}
    for r in results:
        if r.result is Tri.UNKNOWN and r.rule.field not in reasons:
            reasons[r.rule.field] = "birth_date_needed" if r.reason_code == "age_depends_on_birthday" else r.reason_code
    out = []
    for fld, reason in reasons.items():
        if top_with({fld: Tri.PASS}) != top_with({fld: Tri.FAIL}):
            out.append(MissingField(fld, reason, fld in SENSITIVE_FIELDS))
    return out
