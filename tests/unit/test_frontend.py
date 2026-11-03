"""Front-end tests: pure helpers, plus the real Streamlit script run headlessly (AppTest)
against the real API over a seeded temporary database."""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from app.database.connection import make_engine, session_scope
from app.domain.fields import STATE_CODES, STATE_NAMES
from app.main import create_app
from app.services.scheme_loader import load_registry, load_scheme_dir, seed_schemes
from frontend import api_client, ui_helpers as h
from scripts.init_db import init_database

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "data" / "sources" / "registry.yaml")
SCHEMES, _ = load_scheme_dir(ROOT / "data" / "schemes", REGISTRY)
APP = str(ROOT / "frontend" / "app.py")


# ------------------------------------------------------------------ helpers (pure)
def test_build_profile_drops_blanks_but_keeps_false_and_zero():
    p = h.build_profile({"a": None, "b": "", "c": "  ", "d": False, "e": 0, "f": "MH"})
    assert p == {"d": False, "e": 0, "f": "MH"}


def test_rule_icon_inverts_for_exclusions():
    assert h.rule_icon({"result": "PASS", "purpose": "requirement"}) == "✅"
    assert h.rule_icon({"result": "PASS", "purpose": "exclusion"}) == "❌"
    assert h.rule_icon({"result": "FAIL", "purpose": "exclusion"}) == "✅"
    assert h.rule_icon({"result": "UNKNOWN", "purpose": "exclusion"}) == "❓"
    assert h.rule_icon({"result": "OFFICIAL_ONLY", "purpose": "requirement"}) == "🔎"


def test_group_results_fixed_order_and_skips_empty():
    rs = [{"status": "LIKELY_INELIGIBLE"}, {"status": "LIKELY_ELIGIBLE"}, {"status": "LIKELY_ELIGIBLE"}]
    g = h.group_results(rs)
    assert [s for s, _ in g] == ["LIKELY_ELIGIBLE", "LIKELY_INELIGIBLE"] and len(g[0][1]) == 2


def test_question_priorities_counts_schemes_and_is_stable():
    rs = [{"scheme_id": "a", "name": "A", "missing": [{"field": "x", "label": "X", "sensitive": False},
                                                     {"field": "y", "label": "Y", "sensitive": True}]},
          {"scheme_id": "b", "name": "B", "missing": [{"field": "y", "label": "Y", "sensitive": True}]}]
    p = h.question_priorities(rs)
    assert [e["field"] for e in p] == ["y", "x"] and p[0]["schemes"] == ["A", "B"]


def test_format_value():
    assert h.format_value({"type": "money_inr"}, 150000) == "₹1,50,000"
    assert h.format_value({"type": "enum", "value_labels": {"MH": "Maharashtra"}}, "MH") == "Maharashtra"
    assert h.format_value({"type": "bool"}, False) == "No"
    assert h.format_value({"type": "enum"}, "higher_secondary") == "higher secondary"


def test_tree_lines_and_sources():
    tree = {"type": "group", "logic": "AND", "description": None, "items": [
        {"type": "rule", "label": "Age", "requirement": "between 18 and 40 (both included)",
         "checkable_from_profile": True, "source_url": "https://x.gov.in/a", "locator": "para 1"},
        {"type": "rule", "label": "Land", "requirement": "must be yes", "checkable_from_profile": False,
         "source_url": "https://x.gov.in/a", "locator": None}]}
    lines = h.tree_lines(tree)
    assert "All of these must be met" in lines[0] and "para 1" in lines[1]
    assert "cannot be checked from your answers" in lines[2]
    d = {"eligibility": tree, "exclusions": [], "other_conditions": [], "steps": [],
         "documents": [{"source": {"url": "https://x.gov.in/a", "title": "Guidelines"}}]}
    assert h.collect_sources(d) == [{"url": "https://x.gov.in/a", "title": "Guidelines"}]


def test_state_names_match_state_codes():
    assert set(STATE_NAMES) == set(STATE_CODES)


# ------------------------------------------------------------------ API client
def test_client_wraps_error_envelope_and_unreachable(monkeypatch):
    import httpx

    def handler(request):
        return httpx.Response(404, json={"error": {"code": "scheme_not_found", "message": "No scheme", "details": None}})

    monkeypatch.setattr(api_client, "get_client", lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t"))
    with pytest.raises(api_client.ApiProblem) as e:
        api_client.call("GET", "/x")
    assert e.value.code == "scheme_not_found" and e.value.status == 404

    def boom(request):
        raise httpx.ConnectError("secret-host-detail")

    monkeypatch.setattr(api_client, "get_client", lambda: httpx.Client(transport=httpx.MockTransport(boom), base_url="http://t"))
    with pytest.raises(api_client.ApiUnavailable) as e2:
        api_client.call("GET", "/x")
    assert "secret-host-detail" not in str(e2.value)


# ------------------------------------------------------------------ API additions the UI relies on
@pytest.fixture(scope="module")
def api(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('ui') / 'ui.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
    eng.dispose()
    with TestClient(create_app(url, today_provider=lambda: date(2026, 11, 1))) as c:
        yield c


def test_fields_have_value_labels_and_used_by(api):
    fields = {f["key"]: f for f in api.get("/api/v1/fields").json()["fields"]}
    assert fields["state_code"]["value_labels"]["MH"] == "Maharashtra"
    assert fields["age_years"]["value_labels"] is None
    assert "atal-pension-yojana" in fields["age_years"]["used_by"]
    assert fields["district"]["used_by"] == []


def test_exclusion_tree_wording_in_detail(api):
    d = api.get("/api/v1/schemes/atal-pension-yojana").json()
    req = d["exclusions"][0]["items"][0]["requirement"] if d["exclusions"][0]["type"] == "group" else d["exclusions"][0]["requirement"]
    assert req.startswith("excluded if the answer ") and "must be" not in req


# ------------------------------------------------------------------ the real app, headless
@pytest.fixture()
def at(api, monkeypatch):
    monkeypatch.setattr(api_client, "get_client", lambda: api)
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


def test_browse_lists_all_five_schemes_with_real_buttons(at):
    buttons = {b.key for b in at.button}
    assert {f"view_{s.scheme_id}" for s in SCHEMES} <= buttons
    assert len([b for b in buttons if b.startswith("view_")]) == 5


def test_search_filters_the_list(at):
    at.text_input(key="browse_q").set_value("Atal").run()
    assert [b.key for b in at.button if b.key.startswith("view_")] == ["view_atal-pension-yojana"]


def test_detail_view_shows_sources_rules_notice_and_back(at):
    at.button(key="view_atal-pension-yojana").click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Eligibility rules" in text and "between 18 and 40" in text
    assert "excluded if the answer is yes" in text
    assert any("preliminary check" in i.value for i in at.info)
    assert any(w for w in at.warning)  # rules may be incomplete
    at.button(key="back").click().run()
    assert any(b.key == "view_pm-kisan" for b in at.button)


def test_eligibility_form_only_asks_used_fields_and_sensitive_are_grouped(at):
    at.sidebar.radio(key="page").set_value("Check my eligibility").run()
    assert not at.exception
    keys = {w.key for w in list(at.number_input) + list(at.selectbox)}
    assert "q_age_years" in keys and "q_annual_family_income" in keys
    assert "q_district" not in keys and "q_family_size" not in keys  # no scheme uses them


def test_blank_submission_asks_for_information_and_never_assumes(at):
    at.sidebar.radio(key="page").set_value("Check my eligibility").run()
    next(b for b in at.button if b.label == "Check schemes").click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "More information needed" in text and "Likely eligible" not in text and "Likely not eligible" not in text


def test_answers_produce_results_and_clear_resets(at):
    at.sidebar.radio(key="page").set_value("Check my eligibility").run()
    at.number_input(key="q_age_years").set_value(30)
    at.selectbox(key="q_state_code").set_value("MH")
    next(b for b in at.button if b.label == "Check schemes").click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Your results" in " ".join(s.value for s in at.subheader)
    assert "Age" in text
    assert at.session_state["evaluation"]["profile"] == {"age_years": 30, "state_code": "MH"}
    at.button(key="clear").click().run()
    assert at.session_state["evaluation"] is None
    assert at.number_input(key="q_age_years").value is None


def test_about_page_is_honest_about_language(at):
    at.sidebar.radio(key="page").set_value("About").run()
    assert not at.exception
    assert "Only English is available" in " ".join(m.value for m in at.markdown)


def test_api_down_shows_friendly_message(monkeypatch):
    import httpx

    def boom(request):
        raise httpx.ConnectError("x")

    monkeypatch.setattr(api_client, "get_client", lambda: httpx.Client(transport=httpx.MockTransport(boom), base_url="http://t"))
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    assert not app.exception and any("could not reach" in e.value for e in app.error)
