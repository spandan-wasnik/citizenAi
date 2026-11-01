"""Human-readable explanations for rule results. English only for now.

Hindi and Marathi templates arrive in Phase 2 through the same table. Until then an
unsupported language raises, so the system can never silently show English as if it were
the user's language.
"""
from __future__ import annotations

from typing import Any

from app.domain.eligibility import Assessment, Rule, RuleResult, Status, Tri
from app.domain.fields import FIELD_TYPES

PRELIMINARY_NOTICE = {
    "en": ("This is a preliminary check based only on what you told us and on the published rules we have "
           "recorded. It is not an official decision and does not mean the government has approved your "
           "application. Please confirm on the official portal before you apply."),
}

LABELS = {"en": {
    "age_years": "Age", "state_code": "State of residence", "domicile_state_code": "State of domicile",
    "study_state_code": "State of study", "district": "District", "residence_area": "Rural or urban area",
    "annual_family_income": "Annual family income", "annual_individual_income": "Annual individual income",
    "family_size": "Family size", "occupation": "Occupation", "employment_status": "Employment status",
    "education_level": "Education level", "is_enrolled_in_education": "Currently enrolled in education",
    "owns_agricultural_land": "Owns agricultural land", "has_bank_account": "Has a bank account",
    "receives_other_scholarship": "Receives another scholarship", "gender": "Gender",
    "social_category": "Social category", "disability_status": "Disability", "marital_status": "Marital status",
    "is_or_was_income_tax_payer": "Is or has been an income-tax payer",
}}

STATUS_TEXT = {"en": {
    Status.LIKELY_ELIGIBLE: "Likely eligible",
    Status.LIKELY_INELIGIBLE: "Likely not eligible",
    Status.INSUFFICIENT_INFORMATION: "More information needed",
    Status.REQUIRES_OFFICIAL_VERIFICATION: "Needs official verification",
}}

REASON_TEXT = {"en": {
    "record_is_draft": "Our record of this scheme has not been verified against an official source yet.",
    "no_rules": "We have no verified eligibility rules recorded for this scheme.",
    "rules_incomplete": "Our record may not list every official condition.",
    "record_stale": "Our record is overdue for a re-check against the official source.",
    "official_only_rules": "Some conditions can only be confirmed by the official authority.",
    "scheme_status_closed": "This scheme is recorded as closed.",
    "scheme_status_suspended": "This scheme is recorded as suspended.",
    "scheme_status_unknown": "We could not confirm that this scheme is currently active.",
    "deadline_passed": "The recorded application deadline has passed.",
    "confirm_other_conditions": "This scheme has other official conditions that we cannot check from your answers; see the list below.",
}}


def format_inr(amount: int) -> str:
    """Indian digit grouping: 150000 -> ₹1,50,000."""
    s = str(abs(int(amount)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return ("-" if amount < 0 else "") + "₹" + s


def _show(field: str, value: Any) -> str:
    if value is None:
        return "not provided"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if FIELD_TYPES.get(field) == "money_inr" and isinstance(value, int):
        return format_inr(value)
    if isinstance(value, str):
        return value.replace("_", " ")
    return str(value)


def describe_requirement(rule: Rule, lang: str = "en") -> str:
    _check(lang)
    f, v = rule.field, rule.value
    s = lambda x: _show(f, x)  # noqa: E731
    return {
        "eq": lambda: f"must be {s(v)}", "neq": lambda: f"must not be {s(v)}",
        "in": lambda: "must be one of: " + ", ".join(s(x) for x in v),
        "not_in": lambda: "must not be any of: " + ", ".join(s(x) for x in v),
        "gte": lambda: f"at least {s(v)}", "lte": lambda: f"at most {s(v)}",
        "gt": lambda: f"more than {s(v)}", "lt": lambda: f"less than {s(v)}",
        "between": lambda: f"between {s(v[0])} and {s(v[1])} (both included)",
        "is_true": lambda: "must be yes", "is_false": lambda: "must be no",
    }[rule.op]()


def _check(lang: str) -> None:
    if lang not in LABELS:
        raise ValueError(f"explanations are not available in '{lang}' yet; refusing to fall back to English silently")


def explain_result(r: RuleResult, lang: str = "en") -> str:
    _check(lang)
    label = LABELS[lang].get(r.rule.field, r.rule.field)
    req = describe_requirement(r.rule, lang)
    given = _show(r.rule.field, r.user_value)
    code = r.reason_code
    as_of = f" on {r.params['as_of']}" if r.params.get("as_of") else ""

    if r.purpose == "exclusion":
        if r.result is Tri.PASS:
            return f"{label}{as_of}: {given}. This scheme excludes people where this {req}; the exclusion applies to you."
        if r.result is Tri.FAIL:
            return f"{label}{as_of}: {given}. The exclusion ({req}) does not apply to you."
    elif code == "meets_requirement":
        return f"{label}{as_of}: you said {given}; requirement: {req}. This is met."
    elif code == "fails_requirement":
        return f"{label}{as_of}: you said {given}; requirement: {req}. This is not met."

    if code == "missing_value":
        return f"{label}: not provided; requirement: {req}. We need this to decide; we have not assumed it is met."
    if code == "invalid_value":
        return f"{label}: the answer given ({given}) could not be used; requirement: {req}."
    if code == "age_depends_on_birthday":
        ages = " or ".join(str(a) for a in r.params.get("possible_ages", []))
        return (f"Age{as_of}: you could be {ages} on that date depending on your birthday; requirement: {req}. "
                "Your date of birth would settle it.")
    if code == "official_only":
        return f"{label}: requirement: {req}. This cannot be checked from your answers; confirm it with the official authority."
    if code == "rule_not_verified":
        return f"{label}: requirement: {req}. This rule has not been verified against an official source yet."
    return f"{label}: {code}"


def explain_assessment(a: Assessment, lang: str = "en") -> dict:
    """Structured text for display: headline, why, per-rule lines, warnings and the mandatory notice."""
    _check(lang)
    return {
        "headline": STATUS_TEXT[lang][a.status],
        "reasons": [REASON_TEXT[lang].get(c, c) for c in a.reasons],
        "warnings": [REASON_TEXT[lang].get(c, c) for c in a.warnings],
        "rules": [explain_result(r, lang) for r in a.results],
        "missing": [LABELS[lang].get(m.field, m.field) for m in a.missing_fields],
        "confirm": [c.texts[lang] for c in a.other_conditions if lang in c.texts],
        "notice": PRELIMINARY_NOTICE[lang],
    }
