from datetime import date

import pytest

from app.database.connection import make_engine, session_scope
from app.domain.eligibility import Status, Tri, evaluate_scheme
from app.domain.scheme_schema import SchemeFile
from app.services.eligibility_service import (
    SchemeNotFound, condition_applies, evaluate_from_db, from_scheme_file, load_scheme_rules,
)
from scripts.init_db import init_database
from tests.unit.test_scheme_loader import REGISTRY, seed
from tests.unit.test_scheme_schema import base

TODAY = date(2026, 10, 31)


@pytest.fixture
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'e.db'}"
    init_database(url)
    eng = make_engine(url)
    yield eng
    eng.dispose()


def test_file_and_database_loaders_give_identical_rules(engine):
    data = base()
    seed(engine, [data])
    file_rules = from_scheme_file(SchemeFile.model_validate(data))
    with session_scope(engine) as s:
        db_rules = load_scheme_rules(s, "test-scholarship")
    from app.domain.eligibility import rules_hash
    assert rules_hash(file_rules) == rules_hash(db_rules)
    assert (db_rules.status, db_rules.verification_status, db_rules.rules_complete) == ("active", "source_verified", True)


def test_user_scenario_on_the_fictional_scheme(engine):
    seed(engine, [base()])
    profile = {"age_years": 21, "annual_family_income": 150000, "state_code": "MH", "occupation": "student",
               "receives_other_scholarship": False}
    with session_scope(engine) as s:
        a = evaluate_from_db(s, "test-scholarship", profile, today=TODAY)
    assert a.status is Status.LIKELY_ELIGIBLE

    del profile["annual_family_income"]
    with session_scope(engine) as s:
        a = evaluate_from_db(s, "test-scholarship", profile, today=TODAY)
    assert a.status is Status.INSUFFICIENT_INFORMATION
    assert [m.field for m in a.missing_fields] == ["annual_family_income"]


def test_db_and_file_evaluations_agree(engine):
    data = base()
    seed(engine, [data])
    file_rules = from_scheme_file(SchemeFile.model_validate(data))
    for profile in ({}, {"age_years": 40}, {"age_years": 21, "annual_family_income": 1, "occupation": "farmer"},
                    {"age_years": 21, "annual_family_income": 1, "occupation": "student",
                     "receives_other_scholarship": True}):
        with session_scope(engine) as s:
            from_db = evaluate_from_db(s, "test-scholarship", profile, today=TODAY)
        from_file = evaluate_scheme(file_rules, profile, today=TODAY)
        assert (from_db.status, from_db.top, from_db.rules_hash) == (from_file.status, from_file.top, from_file.rules_hash)
        assert sorted((x.rule.field, x.result.value) for x in from_db.results) == \
            sorted((x.rule.field, x.result.value) for x in from_file.results)


def test_draft_scheme_from_database_never_gives_a_verdict(engine):
    data = base()
    data["verification_status"] = "draft"
    data["rules_complete"] = False
    seed(engine, [data])
    with session_scope(engine) as s:
        a = evaluate_from_db(s, "test-scholarship", {"age_years": 99}, today=TODAY)
    assert a.status is Status.REQUIRES_OFFICIAL_VERIFICATION and a.reasons == ("record_is_draft",)
    assert all(x.result is Tri.OFFICIAL for x in a.results)


def test_results_carry_their_sources(engine):
    seed(engine, [base()])
    with session_scope(engine) as s:
        a = evaluate_from_db(s, "test-scholarship", {"age_years": 21}, today=TODAY)
    assert all(x.rule.source_url == "https://pib.gov.in/fixture" and x.rule.locator for x in a.results)


def test_conditional_document_group(engine):
    from sqlalchemy import select
    from app.database import models as m
    seed(engine, [base()])
    with session_scope(engine) as s:
        gid = s.scalar(select(m.RuleGroup.group_id).where(m.RuleGroup.purpose == "conditional"))
        assert condition_applies(s, gid, {"gender": "female"}, today=TODAY) is Tri.PASS
        assert condition_applies(s, gid, {"gender": "male"}, today=TODAY) is Tri.FAIL
        assert condition_applies(s, gid, {}, today=TODAY) is Tri.UNKNOWN


def test_unknown_scheme_raises(engine):
    with session_scope(engine) as s:
        with pytest.raises(SchemeNotFound):
            load_scheme_rules(s, "nope")
