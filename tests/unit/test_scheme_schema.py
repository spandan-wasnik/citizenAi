import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.domain.domains import classify_domain, host_of
from app.domain.fields import PROFILE_FIELDS, STATE_CODES, allowed_values
from app.domain.scheme_schema import SchemeFile

ROOT = Path(__file__).resolve().parents[2]


def base() -> dict:
    """A valid, clearly fictional scheme used as the starting point for each test."""
    return {
        "scheme_id": "test-scholarship", "level": "state", "state_code": "MH",
        "status": "active", "verification_status": "source_verified", "rules_complete": True,
        "last_verified": "2026-10-31",
        "translations": {"en": {"official_name": "Test Scholarship", "origin": "official"}},
        "sources": [{"key": "s1", "url": "https://pib.gov.in/fixture", "doc_type": "html",
                     "retrieved_at": "2026-10-31", "retrieval_method": "manual_curation"}],
        "links": [{"url": "https://pib.gov.in/fixture", "link_type": "information"}],
        "eligibility": {"logic": "AND", "items": [
            {"rule": {"field": "age_years", "op": "between", "value": [18, 30], "source": "s1", "locator": "p1"}},
            {"rule": {"field": "annual_family_income", "op": "lte", "value": 250000, "source": "s1", "locator": "p2"}},
            {"group": {"logic": "OR", "items": [
                {"rule": {"field": "occupation", "op": "eq", "value": "student", "source": "s1", "locator": "p3"}},
                {"rule": {"field": "employment_status", "op": "eq", "value": "unemployed", "source": "s1", "locator": "p3"}},
            ]}},
        ]},
        "exclusions": [{"field": "receives_other_scholarship", "op": "is_true", "source": "s1", "locator": "p4"}],
        "condition_groups": {"women": {"logic": "AND", "items": [
            {"rule": {"field": "gender", "op": "eq", "value": "female", "source": "s1", "locator": "a1"}}]}},
        "documents": [
            {"code": "income_certificate", "requirement_type": "mandatory", "names": {"en": "Income Certificate"},
             "source": "s1", "locator": "a1"},
            {"code": "special_form", "requirement_type": "conditional", "applies_if": "women",
             "names": {"en": "Special form"}, "source": "s1", "locator": "a1"},
        ],
        "steps": {"en": [{"instruction": "Open the portal", "link": "https://pib.gov.in/fixture",
                          "source": "s1", "locator": "s3"}]},
    }


def mutate(**changes) -> dict:
    d = copy.deepcopy(base())
    for key, value in changes.items():
        d[key] = value
    return d


def error_text(data: dict) -> str:
    with pytest.raises(ValidationError) as info:
        SchemeFile.model_validate(data)
    return str(info.value)


def test_valid_scheme_passes_and_rules_are_collected():
    scheme = SchemeFile.model_validate(base())
    assert len(scheme.all_rules()) == 6  # 4 eligibility (incl. 2 nested), 1 condition-group, 1 exclusion
    assert {r.field for r in scheme.all_rules()} >= {"age_years", "occupation", "gender", "receives_other_scholarship"}


def test_template_file_is_valid_and_all_placeholder_urls_flagged():
    raw = yaml.safe_load((ROOT / "data" / "schemes" / "_template.yaml").read_text(encoding="utf-8"))
    scheme = SchemeFile.model_validate(raw)
    assert scheme.verification_status == "draft" and scheme.rules_complete is False
    assert "REPLACE" in scheme.translations["en"].official_name


# ------------------------------------------------------------------ rules
@pytest.mark.parametrize("rule,needle", [
    ({"field": "incom", "op": "lte", "value": 1}, "unknown field"),
    ({"field": "age_years", "op": "is_true"}, "not valid for int"),
    ({"field": "age_years", "op": "lte", "value": "thirty"}, "whole non-negative"),
    ({"field": "age_years", "op": "lte", "value": 30.5}, "whole non-negative"),
    ({"field": "age_years", "op": "lte", "value": -1}, "non-negative"),
    ({"field": "age_years", "op": "lte", "value": True}, "whole non-negative"),
    ({"field": "age_years", "op": "between", "value": [30, 18]}, "low <= high"),
    ({"field": "age_years", "op": "between", "value": [18]}, "[low, high]"),
    ({"field": "age_years", "op": "in", "value": []}, "non-empty list"),
    ({"field": "occupation", "op": "eq", "value": "wizard"}, "not a valid value"),
    ({"field": "state_code", "op": "eq", "value": "XX"}, "not a valid value"),
    ({"field": "state_code", "op": "in", "value": ["MH", "ZZ"]}, "not a valid value"),
    ({"field": "gender", "op": "is_true"}, "not valid for enum"),
    ({"field": "owns_agricultural_land", "op": "eq", "value": True}, "not valid for bool"),
    ({"field": "owns_agricultural_land", "op": "is_true", "value": True}, "takes no value"),
])
def test_invalid_rules_are_rejected(rule, needle):
    rule = {**rule, "source": "s1", "locator": "p1"}
    data = base()
    data["exclusions"] = [rule]
    assert needle in error_text(data)


def test_rule_without_locator_or_with_unknown_source_is_rejected():
    data = base()
    data["exclusions"] = [{"field": "age_years", "op": "gte", "value": 1, "source": "s1", "locator": ""}]
    assert "locator" in error_text(data)
    data["exclusions"] = [{"field": "age_years", "op": "gte", "value": 1, "source": "nope", "locator": "x"}]
    assert "unknown source 'nope'" in error_text(data)


def test_rule_missing_source_key_entirely_is_rejected():
    data = base()
    data["exclusions"] = [{"field": "age_years", "op": "gte", "value": 1, "locator": "x"}]
    assert "source" in error_text(data)


def test_item_must_have_exactly_one_of_rule_or_group():
    data = base()
    data["eligibility"]["items"].append({})
    assert "exactly one" in error_text(data)


def test_unknown_keys_are_rejected_everywhere():
    data = base()
    data["famous_benefit"] = "1 crore"
    assert "famous_benefit" in error_text(data)
    data = base()
    data["eligibility"]["items"][0]["rule"]["valeu"] = 1
    assert "valeu" in error_text(data)


def test_is_true_and_between_and_in_accepted():
    data = base()
    data["exclusions"] = [
        {"field": "owns_agricultural_land", "op": "is_false", "source": "s1", "locator": "x"},
        {"field": "state_code", "op": "in", "value": ["MH", "GJ"], "source": "s1", "locator": "x"},
        {"field": "annual_family_income", "op": "between", "value": [0, 100000], "source": "s1", "locator": "x"},
    ]
    SchemeFile.model_validate(data)


# ------------------------------------------------------------------ scheme-level integrity
def test_state_code_must_match_level():
    assert "central schemes must not have a state_code" in error_text(mutate(level="central"))
    assert "need a state_code" in error_text(mutate(state_code=None))
    assert "not a valid ISO" in error_text(mutate(state_code="ZZ"))


def test_verified_scheme_needs_evidence():
    assert "last_verified is required" in error_text(mutate(last_verified=None))
    assert "at least one source" in error_text(mutate(sources=[], eligibility=None, exclusions=[],
                                                     condition_groups={}, documents=[], steps={}))


def test_volatile_verified_scheme_needs_review_date():
    assert "next_review_due" in error_text(mutate(volatility="volatile"))


def test_rules_complete_cannot_be_claimed_by_draft_or_without_rules():
    assert "cannot be true for a draft" in error_text(mutate(verification_status="draft"))
    d = mutate(eligibility=None, exclusions=[], condition_groups={}, documents=[])
    assert "needs an eligibility rule tree" in error_text(d)


def test_draft_with_unknowns_is_allowed():
    d = {"scheme_id": "bare-draft", "level": "central",
         "translations": {"en": {"official_name": "Bare Draft", "origin": "curated"}}}
    scheme = SchemeFile.model_validate(d)
    assert scheme.status == "unknown" and scheme.eligibility is None and scheme.rules_complete is False


def test_date_logic():
    assert "before last_verified" in error_text(mutate(next_review_due="2026-01-01"))
    assert "before application_open_date" in error_text(
        mutate(application_open_date="2026-12-01", application_deadline="2026-11-01"))


def test_english_name_required():
    assert "English" in error_text(mutate(translations={"hi": {"official_name": "x", "origin": "curated"}}))


@pytest.mark.parametrize("bad", ["Pm_Kisan", "pm kisan", "-x", "x-", "PM-KISAN", ""])
def test_scheme_id_must_be_a_slug(bad):
    assert "slug" in error_text(mutate(scheme_id=bad)) or "scheme_id" in error_text(mutate(scheme_id=bad))


def test_sources_and_links_must_be_unique_and_valid_urls():
    d = base()
    d["sources"].append(copy.deepcopy(d["sources"][0]))
    assert "unique" in error_text(d)
    d = base()
    d["sources"][0]["url"] = "ftp://pib.gov.in/x"
    assert "http(s)" in error_text(d)
    d = base()
    d["links"].append(copy.deepcopy(d["links"][0]))
    assert "duplicate" in error_text(d)


# ------------------------------------------------------------------ links
def test_manual_verification_needs_a_named_person_and_date():
    d = base()
    d["links"][0]["verification_status"] = "verified_manual"
    assert "verified_by" in error_text(d)
    d["links"][0].update(verified_by="spandan", verified_at="2026-10-31")
    SchemeFile.model_validate(d)


def test_non_gov_domain_cannot_be_manually_verified_without_override_and_note():
    d = base()
    d["links"][0].update(url="https://apply.example.com/x", verification_status="verified_manual",
                         verified_by="spandan", verified_at="2026-10-31")
    assert "official_other_verified" in error_text(d)
    d["links"][0]["official_other_verified"] = True
    assert "notes" in error_text(d)
    d["links"][0]["notes"] = "Operated by the department per its official circular"
    d["steps"]["en"][0]["link"] = "https://apply.example.com/x"
    SchemeFile.model_validate(d)


# ------------------------------------------------------------------ documents and steps
def test_conditional_documents_need_a_defined_condition_group():
    d = base()
    d["documents"][1]["applies_if"] = "undefined_group"
    assert "not a defined condition group" in error_text(d)
    d = base()
    del d["documents"][1]["applies_if"]
    assert "applies_if" in error_text(d)
    d = base()
    d["documents"][0]["applies_if"] = "women"
    assert "applies_if" in error_text(d)


def test_documents_need_english_name_source_and_unique_code():
    d = base()
    d["documents"][0]["names"] = {"hi": "आय प्रमाणपत्र"}
    assert "English name" in error_text(d)
    d = base()
    d["documents"].append(copy.deepcopy(d["documents"][0]))
    assert "unique" in error_text(d)
    d = base()
    d["documents"][0]["source"] = "zz"
    assert "unknown source 'zz'" in error_text(d)


def test_step_links_must_be_declared():
    d = base()
    d["steps"]["en"][0]["link"] = "https://pib.gov.in/other"
    assert "not declared in links" in error_text(d)


# ------------------------------------------------------------------ domain helpers and registry
@pytest.mark.parametrize("url,expected", [
    ("https://pmkisan.gov.in/x", "gov_in"), ("https://www.India.gov.in/", "gov_in"),
    ("https://nsap.nic.in", "nic_in"), ("https://gov.in", "gov_in"),
    ("https://evilgov.in", "unrecognized"), ("https://gov.in.evil.com", "unrecognized"),
    ("https://pmkisan.gov.in@evil.com/", "unrecognized"), ("https://notnic.in", "unrecognized"),
    ("not a url", "unrecognized"),
])
def test_domain_classification(url, expected):
    assert classify_domain(host_of(url)) == expected


def test_host_normalisation():
    assert host_of("https://WWW.PMKISAN.gov.in:443/a?b=1") == "pmkisan.gov.in"
    assert host_of("https://pmkisan.gov.in./a") == "pmkisan.gov.in"


def test_field_registry_is_consistent():
    keys = [f[0] for f in PROFILE_FIELDS]
    assert len(keys) == len(set(keys))
    assert len(STATE_CODES) == 36  # 28 states + 8 union territories
    assert "MH" in STATE_CODES and "DL" in STATE_CODES
    assert allowed_values("age_years") is None
    assert allowed_values("state_code") == STATE_CODES
    assert "student" in allowed_values("occupation")


# ------------------------------------------------------------------ other_conditions
def _cond(**kw):
    c = {"kind": "exclusion", "text": {"en": "No family member is a government employee."}, "source": "s1",
         "locator": "para 5"}
    c.update(kw)
    return c


def test_other_conditions_accepted():
    scheme = SchemeFile.model_validate(mutate(other_conditions=[_cond(), _cond(kind="requirement")]))
    assert [c.kind for c in scheme.other_conditions] == ["exclusion", "requirement"]


@pytest.mark.parametrize("bad,needle", [
    ({"source": "nope"}, "unknown source"),
    ({"locator": ""}, "locator"),
    ({"kind": "maybe"}, "kind"),
    ({"text": {"hi": "x"}}, "English"),
    ({"text": {"en": "  "}}, "English"),
])
def test_bad_other_conditions_are_rejected(bad, needle):
    assert needle in error_text(mutate(other_conditions=[_cond(**bad)]))
