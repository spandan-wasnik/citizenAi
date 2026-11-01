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
    assert [r[0] for r in rows] == ["atal-pension-yojana", "pm-kisan"]
