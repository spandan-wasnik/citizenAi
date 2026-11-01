import logging
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database.connection import make_engine, session_scope
from app.main import create_app
from app.services.scheme_loader import load_registry, load_scheme_dir, seed_schemes
from scripts.init_db import init_database

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "data" / "sources" / "registry.yaml")
SCHEMES, ERRORS = load_scheme_dir(ROOT / "data" / "schemes", REGISTRY)
TODAY = date(2026, 11, 1)
SECRET_INCOME = 123457  # a distinctive number to look for in responses and logs


@pytest.fixture(scope="module")
def db_url(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('api') / 'api.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
    eng.dispose()
    return url


@pytest.fixture(scope="module")
def client(db_url):
    app = create_app(db_url, today_provider=lambda: TODAY)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"} and body["error"]["code"] == code
    return body["error"]


# ----------------------------------------------------------------- health and readiness
def test_health_reports_database_and_scheme_count(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["database"] == "ok" and body["schemes"] == len(SCHEMES) == 5
    assert "key" not in str(body).lower().replace("llm_problem", "")  # no secrets of any kind


def test_uninitialised_database_gives_clear_503_and_degraded_health(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'empty.db'}", today_provider=lambda: TODAY)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/health").json()["status"] == "degraded"
        e = err(c.get("/api/v1/schemes"), 503, "database_not_ready")
        assert "init_db" in e["message"]


# ----------------------------------------------------------------- catalogue
def test_list_all_and_filters(client):
    assert client.get("/api/v1/schemes").json()["count"] == 5
    central = client.get("/api/v1/schemes", params={"level": "central"}).json()
    assert {s["scheme_id"] for s in central["schemes"]} == {"pm-kisan", "atal-pension-yojana", "post-matric-scholarship-sc"}
    mh = client.get("/api/v1/schemes", params={"state": "MH"}).json()
    assert mh["count"] == 5  # Maharashtra's two schemes plus the three central ones
    gj = client.get("/api/v1/schemes", params={"state": "GJ"}).json()
    assert {s["scheme_id"] for s in gj["schemes"]} == {"pm-kisan", "atal-pension-yojana", "post-matric-scholarship-sc"}
    assert client.get("/api/v1/schemes", params={"tag": "pension"}).json()["count"] == 1


def test_search_by_text_in_english_and_marathi(client):
    assert [s["scheme_id"] for s in client.get("/api/v1/schemes", params={"q": "Atal"}).json()["schemes"]] == \
        ["atal-pension-yojana"]
    # a word that appears in several schemes' own text matches all of them
    assert "atal-pension-yojana" in {s["scheme_id"] for s in
                                    client.get("/api/v1/schemes", params={"q": "pension"}).json()["schemes"]}
    assert [s["scheme_id"] for s in client.get("/api/v1/schemes", params={"q": "लाडकी"}).json()["schemes"]] == \
        ["mukhyamantri-majhi-ladki-bahin"]
    assert client.get("/api/v1/schemes", params={"q": "zzzz-nothing"}).json()["count"] == 0


def test_list_names_use_requested_language_and_say_when_they_fall_back(client):
    by_id = {s["scheme_id"]: s for s in client.get("/api/v1/schemes", params={"lang": "mr"}).json()["schemes"]}
    assert by_id["mukhyamantri-majhi-ladki-bahin"]["language_used"] == "mr"
    assert by_id["mukhyamantri-majhi-ladki-bahin"]["name"].startswith("मुख्यमंत्री")
    assert by_id["pm-kisan"]["language_used"] == "en"  # no Marathi record yet: honest fallback, never a fake translation


@pytest.mark.parametrize("params", [{"lang": "fr"}, {"level": "galaxy"}, {"state": "maharashtra"},
                                    {"verification_status": "approved"}])
def test_bad_query_values_are_rejected_with_the_standard_error(client, params):
    r = client.get("/api/v1/schemes", params=params)
    assert r.status_code in (400, 422) and "error" in r.json()


def test_detail_has_sources_conditions_documents_steps_and_honest_link_badges(client):
    d = client.get("/api/v1/schemes/pm-kisan").json()
    assert d["translation"]["name"].startswith("Pradhan Mantri Kisan Samman Nidhi")
    assert d["record"]["status"] == "unknown" and d["record"]["rules_complete"] is False
    assert d["eligibility"]["items"][0]["requirement"] == "must be yes"
    assert d["eligibility"]["items"][0]["source_url"].startswith("https://pmkisan.gov.in/")
    assert len(d["other_conditions"]) >= 8 and all(c["locator"] and c["source"]["url"] for c in d["other_conditions"])
    assert {x["code"] for x in d["documents"]} >= {"aadhaar", "land_records"}
    assert [s["number"] for s in d["steps"]] == [1, 2, 3]
    assert d["links"] and all(l["verification_status"] == "unverified" and l["open_in_new_tab"] for l in d["links"])
    assert d["links"][0]["badge"] == "Not verified"
    assert "not an official decision" in d["notice"]


def test_staleness_is_computed_from_the_injected_date(db_url):
    for today, stale in [(date(2026, 12, 1), False), (date(2026, 12, 2), True)]:
        app = create_app(db_url, today_provider=lambda t=today: t)
        with TestClient(app) as c:
            assert c.get("/api/v1/schemes/post-matric-scholarship-sc").json()["record"]["is_stale"] is stale


def test_detail_in_marathi_falls_back_and_says_so(client):
    d = client.get("/api/v1/schemes/mukhyamantri-majhi-ladki-bahin", params={"lang": "mr"}).json()
    assert d["translation"]["language_used"] == "mr" and d["translation"]["short_description"] is None
    assert d["steps"] and d["documents"][0]["name"]  # English where no Marathi text exists yet


def test_unknown_scheme_and_unknown_route_use_the_standard_error(client):
    err(client.get("/api/v1/schemes/nope"), 404, "scheme_not_found")
    err(client.get("/api/v1/nothing-here"), 404, "not_found")
    err(client.post("/api/v1/schemes"), 405, "method_not_allowed")


def test_fields_endpoint_lists_questions_and_flags_sensitive_ones(client):
    fields = {f["key"]: f for f in client.get("/api/v1/fields").json()["fields"]}
    assert fields["gender"]["sensitive"] is True and fields["age_years"]["sensitive"] is False
    assert fields["residence_area"]["allowed_values"] == ["rural", "urban"]


# ----------------------------------------------------------------- evaluation
def test_evaluate_one_scheme(client):
    r = client.post("/api/v1/schemes/post-matric-scholarship-sc/evaluate",
                    json={"profile": {"social_category": "sc", "annual_family_income": 150000}}).json()
    assert r["status"] == "REQUIRES_OFFICIAL_VERIFICATION" and r["top_result"] == "PASS"
    assert {x["code"] for x in r["reasons"]} >= {"rules_incomplete", "scheme_status_unknown"}
    assert any(w["code"] == "confirm_other_conditions" for w in r["warnings"])
    assert len(r["confirm_officially"]) >= 5 and "not an official decision" in r["notice"]
    assert all(x["source_url"] and x["locator"] and x["explanation"] for x in r["rules"])
    assert r["record"]["verification_status"] == "source_verified" and r["engine_version"] == "1"


def test_missing_answers_are_reported_as_questions_to_ask(client):
    r = client.post("/api/v1/schemes/atal-pension-yojana/evaluate", json={"profile": {"age_years": 30}}).json()
    assert r["status"] == "INSUFFICIENT_INFORMATION"
    assert r["missing"] == [{"field": "is_or_was_income_tax_payer", "label": "Is or has been an income-tax payer",
                             "reason": "missing_value", "sensitive": True}]


def test_evaluate_all_orders_and_counts(client):
    body = client.post("/api/v1/evaluate", json={"profile": {
        "gender": "female", "state_code": "MH", "age_years": 30, "annual_family_income": 200000,
        "has_bank_account": True}}).json()
    assert sum(body["counts"].values()) == 5 and len(body["results"]) == 5
    order = {"LIKELY_ELIGIBLE": 0, "INSUFFICIENT_INFORMATION": 1, "REQUIRES_OFFICIAL_VERIFICATION": 2,
             "LIKELY_INELIGIBLE": 3}
    ranks = [order[r["status"]] for r in body["results"]]
    assert ranks == sorted(ranks)
    assert all(r["name"] for r in body["results"]) and body["notice"]


@pytest.mark.parametrize("payload,code", [
    ({"profile": {"favourite_colour": "blue"}}, "unknown_profile_field"),
    ({"profile": {"age_years": "x" * 101}}, "profile_value_too_long"),
    ({"profile": {f"f{i}": 1 for i in range(31)}}, "profile_too_large"),
])
def test_bad_profiles_are_rejected_and_values_never_echoed(client, payload, code):
    r = client.post("/api/v1/evaluate", json=payload)
    err(r, 422, code)
    assert "blue" not in r.text and "xxxx" not in r.text


@pytest.mark.parametrize("payload", [
    {"profile": {"age_years": {"nested": SECRET_INCOME}}}, {"profile": [SECRET_INCOME]},
    {"profile": {}, "extra": SECRET_INCOME}, {"lang": "en"}, "not json",
])
def test_malformed_bodies_give_422_without_echoing_input(client, payload):
    r = client.post("/api/v1/evaluate", json=payload) if not isinstance(payload, str) else \
        client.post("/api/v1/evaluate", content=payload, headers={"content-type": "application/json"})
    err(r, 422, "invalid_request")
    assert str(SECRET_INCOME) not in r.text


def test_hindi_and_marathi_explanations_are_refused_honestly_not_faked(client):
    for lang in ("hi", "mr"):
        e = err(client.post("/api/v1/evaluate", json={"profile": {}, "lang": lang}), 400, "language_not_available")
        assert e["details"]["available"] == ["en"]
    err(client.post("/api/v1/evaluate", json={"profile": {}, "lang": "fr"}), 400, "unsupported_language")


def test_answers_are_not_logged_or_returned_beyond_the_explanations(client, caplog):
    with caplog.at_level(logging.DEBUG):
        r = client.post("/api/v1/schemes/pm-kisan/evaluate",
                        json={"profile": {"owns_agricultural_land": True, "annual_family_income": SECRET_INCOME}})
    assert r.status_code == 200
    assert str(SECRET_INCOME) not in caplog.text
    assert str(SECRET_INCOME) not in r.text  # income is not even a PM-KISAN rule, so it is not echoed back


def test_unexpected_failure_becomes_a_safe_500(db_url, monkeypatch):
    import app.api.routes as routes

    def boom(*a, **k):
        raise RuntimeError(f"secret detail {SECRET_INCOME}")
    monkeypatch.setattr(routes, "evaluate_from_db", boom)
    app = create_app(db_url, today_provider=lambda: TODAY)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/v1/schemes/pm-kisan/evaluate", json={"profile": {}})
    e = err(r, 500, "internal_error")
    assert "secret detail" not in r.text and "Traceback" not in r.text and e["details"] is None
