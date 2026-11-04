import json
from pathlib import Path

import pytest

from app.nlp.extract import extract_profile

GOLD = json.loads((Path(__file__).resolve().parents[2] / "eval" / "extraction_gold.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", GOLD, ids=[c["id"] for c in GOLD])
def test_gold(case):
    r = extract_profile(case["text"], case.get("expected_field"))
    explicit = {e.field: e.value for e in r.extractions if e.provenance == "explicit"}
    inferred = {e.field: e.value for e in r.extractions if e.provenance == "inferred"}
    assert explicit == case.get("explicit", {}), r
    assert inferred == case.get("inferred", {}), r
    assert sorted(c.field for c in r.clarifications) == sorted(case.get("clarify", [])), r
    assert sorted(r.conflicts) == sorted(case.get("conflicts", [])), r


def test_every_extraction_has_evidence_from_the_text_and_inferred_ones_explain_themselves():
    for case in GOLD:
        for e in extract_profile(case["text"], case.get("expected_field")).extractions:
            assert e.evidence.strip()
            if e.provenance == "inferred":
                assert e.note


def test_as_profile_excludes_inferred_unless_asked():
    r = extract_profile("I am a student")
    assert r.as_profile() == {"occupation": "student"}
    assert r.as_profile(include_inferred=True) == {"occupation": "student", "is_enrolled_in_education": True}


def test_extracted_fields_are_all_known_profile_fields():
    from app.domain.fields import FIELD_TYPES
    for case in GOLD:
        for e in extract_profile(case["text"], case.get("expected_field")).extractions:
            assert e.field in FIELD_TYPES


def test_occupation_values_are_allowed_enum_values():
    from app.domain.fields import PROFILE_FIELDS
    allowed = next(v for k, t, v, s, d in PROFILE_FIELDS if k == "occupation")
    for case in GOLD:
        for e in extract_profile(case["text"]).extractions:
            if e.field == "occupation":
                assert e.value in allowed


# ------------------------------------------------------------------ robustness
import time

from hypothesis import given, settings, strategies as st

HOSTILE = ["I am " + "1" * 60 + "x", "I am " + "a " * 200 + "x", "मैं " + "वर्ष" * 80 + "!", "i " + "1 " * 300 + "!",
           "income " + "1" * 1900, "I " + "year " * 390, "I am 1," * 250, "land " * 390, "family income " + "2 lakh " * 200,
           "आय " + "१," * 900]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_input_is_fast(text):
    start = time.perf_counter()
    extract_profile(text)
    assert time.perf_counter() - start < 1.0


def test_over_long_text_is_refused_not_silently_cut():
    with pytest.raises(ValueError):
        extract_profile("a" * 2001)


@settings(max_examples=300, deadline=1000)
@given(st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x97F), max_size=300))
def test_never_crashes_and_always_returns_known_fields(t):
    from app.domain.fields import FIELD_TYPES
    r = extract_profile(t)
    assert all(e.field in FIELD_TYPES for e in r.extractions)
    assert len({e.field for e in r.extractions}) == len(r.extractions)   # one value per field
    assert not set(r.conflicts) & {e.field for e in r.extractions}        # a conflicted field has no value
