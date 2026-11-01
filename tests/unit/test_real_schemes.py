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
