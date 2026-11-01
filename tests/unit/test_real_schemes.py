"""Checks on the real data files in data/schemes (as opposed to the fictional fixtures)."""
from datetime import date
from pathlib import Path

import pytest

from app.database.connection import make_engine, session_scope
from app.domain.eligibility import Status
from app.services.eligibility_service import evaluate_from_db
from app.services.scheme_loader import load_registry, load_scheme_dir, seed_schemes
from scripts.init_db import init_database

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "data" / "sources" / "registry.yaml")
SCHEMES, ERRORS = load_scheme_dir(ROOT / "data" / "schemes", REGISTRY)
TODAY = date(2026, 11, 1)


def test_every_real_scheme_file_is_valid():
    assert not ERRORS, [(e.path, e.messages) for e in ERRORS]


@pytest.mark.parametrize("scheme", SCHEMES, ids=lambda s: s.scheme_id)
def test_real_schemes_are_honest(scheme):
    assert scheme.verification_status in ("draft", "source_verified")  # only the owner sets human_reviewed
    assert scheme.translations["en"].origin != "machine"
    for rule in scheme.all_rules():
        assert rule.locator.strip()
    if scheme.status != "active" or scheme.volatility == "volatile":
        assert scheme.next_review_due is not None or scheme.verification_status == "draft"
    assert not any("REPLACE" in s.url for s in scheme.sources)


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('real') / 'real.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
    yield eng
    eng.dispose()


def run(engine, scheme_id, profile):
    with session_scope(engine) as s:
        return evaluate_from_db(s, scheme_id, profile, today=TODAY)


def test_pm_kisan_never_gives_a_firm_verdict_from_an_outdated_incomplete_record(engine):
    owner = run(engine, "pm-kisan", {"owns_agricultural_land": True})
    assert owner.status is Status.REQUIRES_OFFICIAL_VERIFICATION
    assert {"rules_incomplete", "scheme_status_unknown"} <= set(owner.reasons)
    # a "no land" answer must not produce "likely not eligible" while the record is incomplete
    assert run(engine, "pm-kisan", {"owns_agricultural_land": False}).status is Status.REQUIRES_OFFICIAL_VERIFICATION


def test_pm_kisan_asks_about_land_when_unknown(engine):
    a = run(engine, "pm-kisan", {})
    assert a.status is Status.INSUFFICIENT_INFORMATION
    assert [m.field for m in a.missing_fields] == ["owns_agricultural_land"]
    assert all(r.rule.source_url.startswith("https://pmkisan.gov.in/") and r.rule.locator for r in a.results)


# ----------------------------------------------------------------- atal-pension-yojana
def test_apy_age_boundaries_and_income_tax_exclusion(engine):
    base = {"is_or_was_income_tax_payer": False}
    for age, expected_top in [(17, "FAIL"), (18, "PASS"), (40, "PASS"), (41, "FAIL")]:
        a = run(engine, "atal-pension-yojana", {**base, "age_years": age})
        assert a.top.value == expected_top, age
    excluded = run(engine, "atal-pension-yojana", {"age_years": 30, "is_or_was_income_tax_payer": True})
    assert excluded.top.value == "FAIL"


def test_apy_does_not_give_a_firm_verdict_while_citizenship_is_unmodelled(engine):
    a = run(engine, "atal-pension-yojana", {"age_years": 30, "is_or_was_income_tax_payer": False})
    assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION and "rules_incomplete" in a.reasons
    assert "scheme_status_unknown" not in a.reasons  # status is 'active'


def test_apy_asks_for_the_decisive_missing_answers_and_flags_the_sensitive_one(engine):
    a = run(engine, "atal-pension-yojana", {"age_years": 30})
    assert a.status is Status.INSUFFICIENT_INFORMATION
    assert [(m.field, m.sensitive) for m in a.missing_fields] == [("is_or_was_income_tax_payer", True)]
    # an age outside the range settles it without asking about tax
    out = run(engine, "atal-pension-yojana", {"age_years": 50})
    assert out.missing_fields == ()


def test_shared_document_code_is_consistent_across_schemes(engine):
    from sqlalchemy import text
    with session_scope(engine) as s:
        rows = s.execute(text("SELECT scheme_id, requirement_type FROM scheme_documents "
                              "WHERE document_code='aadhaar' ORDER BY scheme_id")).all()
    assert [r[0] for r in rows] == ["atal-pension-yojana", "mukhyamantri-majhi-ladki-bahin", "pm-kisan", "post-matric-scholarship-sc"]


# ----------------------------------------------------------------- post-matric-scholarship-sc
PMS = "post-matric-scholarship-sc"


def test_pms_sc_rules_and_boundaries(engine):
    ok = {"social_category": "sc", "annual_family_income": 250000}
    assert run(engine, PMS, ok).top.value == "PASS"
    assert run(engine, PMS, {**ok, "annual_family_income": 250001}).top.value == "FAIL"
    assert run(engine, PMS, {**ok, "social_category": "obc"}).top.value == "FAIL"


def test_pms_sc_holding_another_scholarship_is_not_an_exclusion(engine):
    # guidelines para 5.8: the student chooses the more beneficial scholarship; it is not a disqualification
    a = run(engine, PMS, {"social_category": "sc", "annual_family_income": 100000, "receives_other_scholarship": True})
    assert a.top.value == "PASS"


def test_pms_sc_never_gives_a_firm_verdict_and_flags_the_sensitive_question(engine):
    a = run(engine, PMS, {"social_category": "sc", "annual_family_income": 100000})
    assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION
    assert {"rules_incomplete", "scheme_status_unknown"} <= set(a.reasons)
    b = run(engine, PMS, {"annual_family_income": 100000})
    assert b.status is Status.INSUFFICIENT_INFORMATION
    assert [(m.field, m.sensitive) for m in b.missing_fields] == [("social_category", True)]
    assert all("Postmatric_SC.pdf" in r.rule.source_url for r in b.results)


# ----------------------------------------------------------------- mukhyamantri-majhi-ladki-bahin
LB = "mukhyamantri-majhi-ladki-bahin"
WOMAN = {"gender": "female", "state_code": "MH", "age_years": 30, "annual_family_income": 200000, "has_bank_account": True}


def test_ladki_bahin_definite_failures(engine):
    assert run(engine, LB, {**WOMAN, "age_years": 20}).top.value == "FAIL"
    assert run(engine, LB, {**WOMAN, "gender": "male"}).top.value == "FAIL"
    assert run(engine, LB, {**WOMAN, "state_code": "GJ"}).top.value == "FAIL"
    assert run(engine, LB, {**WOMAN, "annual_family_income": 250001}).top.value == "FAIL"
    assert run(engine, LB, {**WOMAN, "has_bank_account": False}).top.value == "FAIL"


def test_ladki_bahin_upper_age_is_never_a_definite_no(engine):
    # the age ceiling is official_only: an amendment to 65 is reported but unread
    for age in (59, 60, 64, 70):
        a = run(engine, LB, {**WOMAN, "age_years": age})
        assert a.top.value == "OFFICIAL_ONLY", age
        assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION
    assert run(engine, LB, {**WOMAN, "age_years": 21}).top.value == "OFFICIAL_ONLY"
    assert run(engine, LB, {**WOMAN, "age_years": 30}).results[3].reason_code == "official_only"


def test_ladki_bahin_asks_only_decisive_questions_and_marks_gender_sensitive(engine):
    a = run(engine, LB, {"state_code": "MH", "age_years": 30})
    assert a.status is Status.INSUFFICIENT_INFORMATION
    assert {(m.field, m.sensitive) for m in a.missing_fields} == {
        ("gender", True), ("annual_family_income", False), ("has_bank_account", False)}
    assert run(engine, LB, {"state_code": "GJ"}).missing_fields == ()  # outside Maharashtra: nothing else matters


def test_ladki_bahin_has_no_invented_links_and_cites_its_source(engine):
    scheme = next(s for s in SCHEMES if s.scheme_id == LB)
    assert scheme.links == [] and scheme.application_deadline is None
    assert scheme.sources[0].url == "https://www.maharashtra.gov.in/"  # the real exact PDF address is not known yet
    assert "28 June 2024" in scheme.sources[0].title


# ----------------------------------------------------------------- rajarshi-shahu-maharaj-ebc-fee-concession
EBC = "rajarshi-shahu-maharaj-ebc-fee-concession"
STUDENT = {"domicile_state_code": "MH", "is_enrolled_in_education": True, "annual_family_income": 150000,
           "receives_other_scholarship": False}


def test_ebc_rules_and_boundaries(engine):
    assert run(engine, EBC, STUDENT).top.value == "PASS"
    assert run(engine, EBC, {**STUDENT, "annual_family_income": 800000}).top.value == "PASS"
    assert run(engine, EBC, {**STUDENT, "annual_family_income": 800001}).top.value == "FAIL"
    assert run(engine, EBC, {**STUDENT, "domicile_state_code": "GJ"}).top.value == "FAIL"
    assert run(engine, EBC, {**STUDENT, "is_enrolled_in_education": False}).top.value == "FAIL"
    assert run(engine, EBC, {**STUDENT, "receives_other_scholarship": True}).top.value == "FAIL"  # Eligibility (f)


def test_ebc_never_gives_a_firm_verdict_and_asks_the_right_questions(engine):
    assert run(engine, EBC, STUDENT).status is Status.REQUIRES_OFFICIAL_VERIFICATION
    a = run(engine, EBC, {"domicile_state_code": "MH"})
    assert a.status is Status.INSUFFICIENT_INFORMATION
    assert {m.field for m in a.missing_fields} == {"is_enrolled_in_education", "annual_family_income",
                                                  "receives_other_scholarship"}


def test_shared_document_names_do_not_clash_between_schemes(engine):
    from sqlalchemy import text
    with session_scope(engine) as s:
        rows = s.execute(text("SELECT document_code, COUNT(*) FROM document_translations GROUP BY document_code, lang "
                              "HAVING COUNT(*) > 1")).all()
    assert rows == []


# ----------------------------------------------------------------- other conditions in the real data
@pytest.mark.parametrize("scheme", SCHEMES, ids=lambda s: s.scheme_id)
def test_every_real_scheme_lists_its_unchecked_conditions_with_sources(scheme):
    assert scheme.other_conditions, "every real scheme here has official conditions the engine cannot check"
    for c in scheme.other_conditions:
        assert c.locator.strip() and c.text["en"].strip()


def test_pm_kisan_and_ladki_bahin_exclusions_are_listed_not_modelled(engine):
    pm = run(engine, "pm-kisan", {"owns_agricultural_land": True})
    assert len(pm.other_conditions) >= 8 and "confirm_other_conditions" in pm.warnings
    assert sum(c.kind == "exclusion" for c in run(engine, LB, WOMAN).other_conditions) == 7
