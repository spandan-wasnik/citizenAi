"""Schema for data/schemes/<scheme_id>.yaml. Pure validation: no database, no network.

Design rules enforced here (each is tested):
  * every rule, document and step cites a source declared in the same file, plus a locator;
  * rule fields and values must exist in the field registry (typos fail loudly);
  * unknown facts are left out or null, never guessed; unknown keys are rejected;
  * a scheme cannot claim to be verified, or have complete rules, without evidence;
  * links to non-.gov.in/.nic.in domains need an explicit, explained official override.
Money is whole rupees ("1.5 lakh" is written 150000). Ranges are inclusive.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.domains import classify_domain, host_of
from app.domain.fields import FIELD_TYPES, STATE_CODES, allowed_values
from app.domain.link_policy import assess_url

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
CODE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
Operator = Literal["eq", "neq", "in", "not_in", "gte", "lte", "gt", "lt", "between", "is_true", "is_false"]
Lang = Literal["en", "hi", "mr"]

_NUMERIC_OPS = {"eq", "neq", "in", "not_in", "gte", "lte", "gt", "lt", "between"}
_ALLOWED_OPS = {
    "int": _NUMERIC_OPS, "money_inr": _NUMERIC_OPS,
    "enum": {"eq", "neq", "in", "not_in"}, "string": {"eq", "neq", "in", "not_in"},
    "bool": {"is_true", "is_false"},
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class RuleSpec(Strict):
    field: str
    op: Operator
    value: Any = None
    as_of: date | None = None
    evaluability: Literal["auto", "official_only"] = "auto"
    source: str
    locator: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_against_field_registry(self) -> "RuleSpec":
        vtype = FIELD_TYPES.get(self.field)
        if vtype is None:
            raise ValueError(f"unknown field '{self.field}' (not in the profile-field registry)")
        if self.op not in _ALLOWED_OPS[vtype]:
            raise ValueError(f"operator '{self.op}' is not valid for {vtype} field '{self.field}'")
        v, op = self.value, self.op
        if vtype == "bool":
            if v is not None:
                raise ValueError(f"'{op}' takes no value")
            return self
        if op in ("in", "not_in"):
            if not isinstance(v, list) or not v:
                raise ValueError(f"'{op}' needs a non-empty list")
            items = v
        elif op == "between":
            if not (isinstance(v, list) and len(v) == 2 and all(_is_int(x) for x in v)) or v[0] > v[1]:
                raise ValueError("'between' needs [low, high] integers with low <= high (inclusive)")
            items = []
        else:
            items = [v]
        for item in items:
            if vtype in ("int", "money_inr"):
                if not _is_int(item) or item < 0:
                    raise ValueError(f"'{self.field}' needs whole non-negative numbers (rupees for money), got {item!r}")
            elif not isinstance(item, str) or not item:
                raise ValueError(f"'{self.field}' needs text values, got {item!r}")
            elif vtype == "enum" and item not in (allowed_values(self.field) or ()):
                raise ValueError(f"'{item}' is not a valid value for '{self.field}'")
        return self


class GroupSpec(Strict):
    logic: Literal["AND", "OR"]
    description: str | None = None
    items: list["Item"] = Field(min_length=1)


class Item(Strict):
    rule: RuleSpec | None = None
    group: GroupSpec | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "Item":
        if (self.rule is None) == (self.group is None):
            raise ValueError("each item must contain exactly one of 'rule' or 'group'")
        return self


GroupSpec.model_rebuild()


class SourceSpec(Strict):
    key: str
    url: str
    title: str | None = None
    doc_type: Literal["html", "pdf", "docx", "json", "csv", "curated_note"]
    retrieved_at: date
    retrieval_method: Literal["manual_curation", "admin_upload", "automated_fetch"]
    language: Lang | None = None

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("must be an http(s) URL with a host")
        return v


class LinkSpec(Strict):
    url: str
    link_type: Literal["application", "information", "status_tracking", "helpline", "guidelines"]
    verification_status: Literal["unverified", "verified_manual"] = "unverified"
    verified_by: str | None = None
    verified_at: date | None = None
    official_other_verified: bool = False
    notes: str | None = None

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("must be an http(s) URL with a host")
        return v

    @model_validator(mode="after")
    def _verification_rules(self) -> "LinkSpec":
        assessment = assess_url(self.url)
        if assessment.suspicious:
            raise ValueError(f"link is flagged suspicious ({', '.join(assessment.danger)}); it cannot be used")
        if self.verification_status == "verified_manual" and not (self.verified_by and self.verified_at):
            raise ValueError("verified_manual needs verified_by and verified_at")
        if self.official_other_verified and not self.notes:
            raise ValueError("official_other_verified needs notes explaining why the domain is official")
        if (self.verification_status == "verified_manual"
                and classify_domain(host_of(self.url)) == "unrecognized" and not self.official_other_verified):
            raise ValueError("a non-.gov.in/.nic.in link can only be verified_manual with "
                             "official_other_verified: true and an explanatory note")
        return self


class TranslationSpec(Strict):
    official_name: str = Field(min_length=1)
    short_description: str | None = None
    benefits: str | None = None
    target_beneficiaries: str | None = None
    origin: Literal["official", "curated", "machine"]


class DocumentSpec(Strict):
    code: str
    requirement_type: Literal["mandatory", "conditional", "optional"]
    applies_if: str | None = None
    purpose_note: str | None = None
    names: dict[Lang, str]
    explanations: dict[Lang, str] = Field(default_factory=dict)
    source: str
    locator: str = Field(min_length=1)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not CODE.match(v):
            raise ValueError("document code must be lower_snake_case")
        return v

    @model_validator(mode="after")
    def _names_and_condition(self) -> "DocumentSpec":
        if "en" not in self.names:
            raise ValueError("documents need an English name")
        if (self.requirement_type == "conditional") != (self.applies_if is not None):
            raise ValueError("'applies_if' is required for (and only for) conditional documents")
        return self


class StepSpec(Strict):
    instruction: str = Field(min_length=1)
    link: str | None = None  # a url declared in 'links'
    source: str
    locator: str = Field(min_length=1)


class OtherConditionSpec(Strict):
    """An official condition that cannot be settled from profile answers. Shown as 'confirm this officially'."""
    kind: Literal["exclusion", "requirement"]
    text: dict[Lang, str]
    source: str
    locator: str = Field(min_length=1)

    @model_validator(mode="after")
    def _english_required(self) -> "OtherConditionSpec":
        if not self.text.get("en", "").strip():
            raise ValueError("an other-condition needs English text")
        return self


class SchemeFile(Strict):
    scheme_id: str
    level: Literal["central", "state", "ut", "local"]
    state_code: str | None = None
    ministry: str | None = None
    department: str | None = None
    status: Literal["active", "closed", "suspended", "unknown"] = "unknown"
    volatility: Literal["stable", "volatile"] = "stable"
    verification_status: Literal["draft", "source_verified", "human_reviewed"] = "draft"
    rules_complete: bool = False
    last_verified: date | None = None
    next_review_due: date | None = None
    application_open_date: date | None = None
    application_deadline: date | None = None
    tags: list[str] = Field(default_factory=list)
    translations: dict[Lang, TranslationSpec]
    sources: list[SourceSpec] = Field(default_factory=list)
    links: list[LinkSpec] = Field(default_factory=list)
    eligibility: GroupSpec | None = None
    exclusions: list[RuleSpec] = Field(default_factory=list)
    other_conditions: list[OtherConditionSpec] = Field(default_factory=list)
    condition_groups: dict[str, GroupSpec] = Field(default_factory=dict)
    documents: list[DocumentSpec] = Field(default_factory=list)
    steps: dict[Lang, list[StepSpec]] = Field(default_factory=dict)

    @field_validator("scheme_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG.match(v):
            raise ValueError("scheme_id must be a lowercase slug such as 'pm-kisan'")
        return v

    # -- helpers ---------------------------------------------------------------------------
    def all_rules(self) -> list[RuleSpec]:
        out: list[RuleSpec] = []

        def walk(group: GroupSpec) -> None:
            for item in group.items:
                if item.rule:
                    out.append(item.rule)
                else:
                    walk(item.group)  # type: ignore[arg-type]

        for g in ([self.eligibility] if self.eligibility else []) + list(self.condition_groups.values()):
            walk(g)
        return out + list(self.exclusions)

    # -- cross-field checks ----------------------------------------------------------------
    @model_validator(mode="after")
    def _cross_checks(self) -> "SchemeFile":
        problems: list[str] = []

        if "en" not in self.translations:
            problems.append("an English translation (name) is required")
        if self.level == "central" and self.state_code is not None:
            problems.append("central schemes must not have a state_code")
        if self.level != "central" and self.state_code is None:
            problems.append(f"{self.level} schemes need a state_code")
        if self.state_code is not None and self.state_code not in STATE_CODES:
            problems.append(f"state_code '{self.state_code}' is not a valid ISO 3166-2:IN code")

        keys = [s.key for s in self.sources]
        if len(keys) != len(set(keys)):
            problems.append("source keys must be unique")
        urls = [s.url for s in self.sources]
        if len(urls) != len(set(urls)):
            problems.append("source urls must be unique")
        known = set(keys)

        for rule in self.all_rules():
            if rule.source not in known:
                problems.append(f"rule on '{rule.field}' cites unknown source '{rule.source}'")
        for cond in self.other_conditions:
            if cond.source not in known:
                problems.append(f"other condition '{cond.text.get('en', '')[:30]}' cites unknown source '{cond.source}'")
        for doc in self.documents:
            if doc.source not in known:
                problems.append(f"document '{doc.code}' cites unknown source '{doc.source}'")
            if doc.applies_if and doc.applies_if not in self.condition_groups:
                problems.append(f"document '{doc.code}' applies_if '{doc.applies_if}' is not a defined condition group")
        if len({d.code for d in self.documents}) != len(self.documents):
            problems.append("document codes must be unique within a scheme")

        link_keys = {(l.url, l.link_type) for l in self.links}
        if len(link_keys) != len(self.links):
            problems.append("duplicate (url, link_type) in links")
        link_urls = {l.url for l in self.links}
        for lang, steps in self.steps.items():
            for i, step in enumerate(steps, 1):
                if step.source not in known:
                    problems.append(f"{lang} step {i} cites unknown source '{step.source}'")
                if step.link and step.link not in link_urls:
                    problems.append(f"{lang} step {i} links to '{step.link}', which is not declared in links")

        if self.verification_status != "draft":
            if self.last_verified is None:
                problems.append("last_verified is required when verification_status is not 'draft'")
            if not self.sources:
                problems.append("a verified scheme needs at least one source")
            if self.volatility == "volatile" and self.next_review_due is None:
                problems.append("volatile schemes need next_review_due")
        if self.rules_complete:
            if self.verification_status == "draft":
                problems.append("rules_complete cannot be true for a draft scheme")
            if self.eligibility is None:
                problems.append("rules_complete needs an eligibility rule tree")
        if self.last_verified and self.next_review_due and self.next_review_due < self.last_verified:
            problems.append("next_review_due is before last_verified")
        if (self.application_open_date and self.application_deadline
                and self.application_deadline < self.application_open_date):
            problems.append("application_deadline is before application_open_date")

        if problems:
            raise ValueError("; ".join(problems))
        return self
