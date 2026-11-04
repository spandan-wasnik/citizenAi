"""Human-readable explanations for rule results in English, Hindi and Marathi.

All wording lives in the language catalogs (app/i18n/data/<lang>.json, section "explain") so a native speaker can
review it. An unsupported language raises, so the system can never silently show English as if it were the user's
language. Official 'other conditions' are shown in the requested language when we hold that translation; otherwise the
English official text is shown and labelled as English (it is never dropped, and never machine-translated).
"""
from __future__ import annotations

from typing import Any

from app.domain.eligibility import Assessment, Rule, RuleResult, Status, Tri
from app.domain.fields import FIELD_TYPES
from app.domain.money import format_inr  # noqa: F401  (re-exported: other modules import it from here)
from app.i18n.catalog import LANGS, PROTECTED_TERMS, catalog

STATE_FIELDS = {"state_code", "domicile_state_code", "study_state_code"}


class _Lazy(dict):
    """dict[lang] -> catalog-derived table, built on first use so importing this module stays cheap."""

    def __init__(self, build):
        super().__init__()
        self._build = build

    def __missing__(self, lang: str):
        if lang not in LANGS:
            raise KeyError(lang)
        self[lang] = self._build(lang)
        return self[lang]

    def __contains__(self, lang) -> bool:
        return lang in LANGS


LABELS = _Lazy(lambda l: catalog(l)["labels"])
STATUS_TEXT = _Lazy(lambda l: {Status(k): v for k, v in catalog(l)["explain"]["status"].items()})
REASON_TEXT = _Lazy(lambda l: catalog(l)["explain"]["reasons"])
PRELIMINARY_NOTICE = _Lazy(lambda l: catalog(l)["explain"]["notice"])


def _check(lang: str) -> None:
    if lang not in LANGS:
        raise ValueError(f"explanations are not available in '{lang}'; refusing to fall back to another language silently")


def _show(field: str, value: Any, lang: str) -> str:
    c = catalog(lang)
    if value is None:
        return c["explain"]["not_provided"]
    if isinstance(value, bool):
        return (c["yes"] if value else c["no"]).lower()
    if field in STATE_FIELDS:
        return c["states"].get(str(value), str(value))
    if FIELD_TYPES.get(field) == "money_inr" and isinstance(value, int):
        return format_inr(value)
    if isinstance(value, str):
        text = c["values"].get(field, {}).get(value, value.replace("_", " "))
        return text if lang != "en" or text in PROTECTED_TERMS else text.lower()
    return str(value)


def _fill(template: str, rule: Rule, lang: str) -> str:
    f, v = rule.field, rule.value
    if rule.op in ("in", "not_in"):
        return template.format(vs=", ".join(_show(f, x, lang) for x in v))
    if rule.op == "between":
        return template.format(a=_show(f, v[0], lang), b=_show(f, v[1], lang))
    return template.format(v=_show(f, v, lang)) if "{v}" in template else template


def describe_requirement(rule: Rule, lang: str = "en") -> str:
    _check(lang)
    return _fill(catalog(lang)["explain"]["req"][rule.op], rule, lang)


def describe_exclusion(rule: Rule, lang: str = "en") -> str:
    """The excluding condition, phrased so it reads correctly after 'excludes people whose answer ...'."""
    _check(lang)
    return _fill(catalog(lang)["explain"]["excl"][rule.op], rule, lang)


def explain_result(r: RuleResult, lang: str = "en") -> str:
    _check(lang)
    e = catalog(lang)["explain"]
    t = e["result"]
    label = LABELS[lang].get(r.rule.field, r.rule.field)
    given = _show(r.rule.field, r.user_value, lang)
    code = r.reason_code
    as_of = e["as_of"].format(date=r.params["as_of"]) if r.params.get("as_of") else ""

    if r.purpose == "exclusion":
        rule = t["excl_rule"].format(cond=describe_exclusion(r.rule, lang))
        kw = dict(label=label, as_of=as_of, given=given, rule=rule)
        if r.result is Tri.PASS:
            return t["excl_applies"].format(**kw)
        if r.result is Tri.FAIL:
            return t["excl_not_applies"].format(**kw)
        key = {"missing_value": "excl_missing", "invalid_value": "excl_invalid", "official_only": "excl_official_only",
               "rule_not_verified": "excl_not_verified"}.get(code)
        return t[key].format(**kw) if key else f"{label}: {code}"

    kw = dict(label=label, as_of=as_of, given=given, req=describe_requirement(r.rule, lang))
    if code == "age_depends_on_birthday":
        kw["ages"] = e["or"].join(str(a) for a in r.params.get("possible_ages", []))
    key = {"meets_requirement": "meets", "fails_requirement": "fails", "missing_value": "missing", "invalid_value": "invalid",
           "age_depends_on_birthday": "age_depends", "official_only": "official_only",
           "rule_not_verified": "not_verified"}.get(code)
    return t[key].format(**kw) if key else f"{label}: {code}"


def confirm_list(a: Assessment, lang: str) -> list[dict[str, Any]]:
    """Official conditions we cannot check, in the requested language when we hold it, else in labelled English."""
    out = []
    for c in a.other_conditions:
        used = lang if lang in c.texts else "en" if "en" in c.texts else None
        if used:
            out.append({"kind": c.kind, "text": c.texts[used], "language_used": used,
                        "source_url": c.source_url, "locator": c.locator})
    return out


def explain_assessment(a: Assessment, lang: str = "en") -> dict[str, Any]:
    """Structured text for display: headline, why, per-rule lines, warnings and the mandatory notice."""
    _check(lang)
    return {
        "headline": STATUS_TEXT[lang][a.status],
        "reasons": [REASON_TEXT[lang].get(c, c) for c in a.reasons],
        "warnings": [REASON_TEXT[lang].get(c, c) for c in a.warnings],
        "rules": [explain_result(r, lang) for r in a.results],
        "missing": [LABELS[lang].get(m.field, m.field) for m in a.missing_fields],
        "confirm": confirm_list(a, lang),
        "notice": PRELIMINARY_NOTICE[lang],
    }
