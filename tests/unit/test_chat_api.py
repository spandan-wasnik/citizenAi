import json
import logging
import re
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
SCHEMES, _ = load_scheme_dir(ROOT / "data" / "schemes", REGISTRY)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('chat') / 'chat.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
    eng.dispose()
    with TestClient(create_app(url, today_provider=lambda: date(2026, 11, 5))) as c:
        yield c


def say(client, message="", state=None, language=None):
    body = {"message": message}
    if state is not None:
        body["state"] = state
    if language:
        body["language"] = language
    r = client.post("/api/v1/chat", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def kinds(reply):
    return [a["kind"] for a in reply["actions"]]


# ------------------------------------------------------------------ starting
def test_empty_message_starts_a_conversation_with_greeting_and_first_question(client):
    r = say(client)
    assert kinds(r)[:2] == ["greeting", "ask_field"] and len(r["messages"]) == 2 and r["language"] == "en"
    assert r["state"]["expected_field"] == "annual_family_income" and not r["done"] and r["results"] is None


@pytest.mark.parametrize("lang,word", [("hi", "नमस्ते"), ("mr", "नमस्कार")])
def test_chosen_language_starts_the_conversation_in_that_language(client, lang, word):
    r = say(client, language=lang)
    assert r["language"] == lang and word in r["messages"][0]


def test_unknown_language_is_refused(client):
    r = client.post("/api/v1/chat", json={"message": "", "language": "fr"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_language"


# ------------------------------------------------------------------ whole conversations through HTTP
OPENING = {"en": "I am a 30 year old woman and I live in Maharashtra",
           "hi": "मैं ३० साल की महिला हूँ और महाराष्ट्र में रहती हूँ",
           "mr": "मी ३० वर्षांची आहे आणि मी महाराष्ट्रात राहते"}
REPLIES = {
    "en": {"annual_family_income": "2.5 lakh", "domicile_state_code": "Maharashtra", "is_enrolled_in_education": "no",
           "receives_other_scholarship": "no", "owns_agricultural_land": "no", "has_bank_account": "yes",
           "social_category": "general", "is_or_was_income_tax_payer": "no", "gender": "female"},
    "hi": {"annual_family_income": "ढाई लाख", "domicile_state_code": "महाराष्ट्र", "is_enrolled_in_education": "नहीं",
           "receives_other_scholarship": "नहीं", "owns_agricultural_land": "नहीं", "has_bank_account": "हाँ",
           "social_category": "सामान्य वर्ग", "is_or_was_income_tax_payer": "नहीं", "gender": "महिला"},
    "mr": {"annual_family_income": "अडीच लाख", "domicile_state_code": "महाराष्ट्र", "is_enrolled_in_education": "नाही",
           "receives_other_scholarship": "नाही", "owns_agricultural_land": "नाही", "has_bank_account": "होय",
           "social_category": "खुला प्रवर्ग", "is_or_was_income_tax_payer": "नाही", "gender": "महिला"},
}
ALLOWED_LATIN = re.compile(r"\b(SC|ST|OBC|EWS|skip|PM|APY)\b")


@pytest.mark.parametrize("lang", OPENING)
def test_full_conversation_over_http_ends_with_results_in_the_same_language(client, lang):
    r = say(client, OPENING[lang])
    transcript = list(r["messages"])
    for _ in range(15):
        if r["done"]:
            break
        r = say(client, REPLIES[lang][r["state"]["expected_field"]], r["state"])
        transcript += r["messages"]
    assert r["done"] and r["results"] and len(r["results"]["results"]) == 5
    assert r["language"] == lang
    if lang != "en":
        text = ALLOWED_LATIN.sub("", " ".join(transcript))
        assert not re.search("[A-Za-z]", text), text
        assert not re.search("[A-Za-z]", re.sub(r"\b(SC|ST|OBC|EWS)\b", "", r["results"]["notice"]))
        assert all(i["headline"] for i in r["results"]["results"])
    assert r["state"]["answers"]["age_years"] == [30, "explicit"]


# ------------------------------------------------------------------ information questions
def test_documents_question_is_answered_from_verified_records_with_sources(client):
    start = say(client)
    r = say(client, "What documents do I need for PM Kisan?", start["state"])
    assert r["info"][0]["scheme_id"] == "pm-kisan" and r["info"][0]["intent"] == "documents"
    text = "\n".join(r["messages"])
    assert "Documents recorded for" in text and "source:" in text
    assert "ask_field" not in kinds(r) and r["state"]["expected_field"] == start["state"]["expected_field"]


def test_information_in_hindi_labels_untranslated_text_as_english(client):
    r = say(client, "पीएम किसान के लिए कौन से दस्तावेज चाहिए?", language="hi")
    text = "\n".join(r["messages"])
    assert r["language"] == "hi" and "दर्ज दस्तावेज़" in text and "अंग्रेज़ी में दिखाया गया" in text


def test_how_to_apply_and_links_never_invent_an_address(client):
    r = say(client, "how do I apply for Ladki Bahin")
    assert r["info"][0]["scheme_id"] == "mukhyamantri-majhi-ladki-bahin"
    urls = {l["url"] for i in r["info"] for l in i["links"]}
    known = {l.url for s in SCHEMES for l in s.links}
    assert urls <= known
    r2 = say(client, "official website for Ladki Bahin")
    assert ("We do not guess web addresses" in "\n".join(r2["messages"])) == (not r2["info"][0]["links"])


def test_scheme_info_help_thanks_and_which_scheme(client):
    assert "Atal" in "\n".join(say(client, "Tell me about Atal Pension Yojana")["messages"])
    assert "Here you can check" in say(client, "help")["messages"][0]
    assert "You are welcome" in say(client, "thank you")["messages"][0]
    assert "Which scheme" in "\n".join(say(client, "what documents do I need")["messages"])


def test_scheme_named_earlier_is_remembered_for_the_next_information_question(client):
    r = say(client, "Am I eligible for PM Kisan?")
    r = say(client, "what documents do I need", r["state"])
    assert r["info"][0]["scheme_id"] == "pm-kisan"


# ------------------------------------------------------------------ the state comes from outside: validate hard
BAD_STATES = [
    {"answers": {"nope": ["SECRET123", "explicit"]}}, {"answers": {"age_years": ["SECRET123", "explicit"]}},
    {"answers": {"age_years": [30, "trusted"]}}, {"answers": {"age_years": [True, "explicit"]}},
    {"answers": {"age_years": [-1, "explicit"]}}, {"answers": {"gender": ["SECRET123", "explicit"]}},
    {"answers": {"state_code": ["ZZ", "explicit"]}}, {"answers": {"gender": ["female"]}},
    {"language": "SECRET123"}, {"SECRET123": 1}, {"pending_confirm": ["age_years"]},
    {"answers": {"age_years": [30, "explicit"]}, "pending_confirm": ["age_years"]},
    {"skipped": ["SECRET123"]}, {"expected_field": "SECRET123"}, {"fail_counts": {"age_years": "SECRET123"}},
    {"pending_income": "SECRET123"}, {"focus": ["x"] * 6}, {"turns": -1}, {"answers": "SECRET123"},
    {"answers": {f"f{i}": [1, "explicit"] for i in range(40)}},
]


@pytest.mark.parametrize("state", BAD_STATES, ids=range(len(BAD_STATES)))
def test_invalid_state_is_refused_and_never_echoed(client, state):
    r = client.post("/api/v1/chat", json={"message": "hi", "state": state})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_state"
    assert "SECRET123" not in r.text


def test_unknown_request_fields_and_oversized_messages_are_refused(client):
    assert client.post("/api/v1/chat", json={"message": "x", "admin": True}).status_code == 422
    assert client.post("/api/v1/chat", json={"message": "x" * 10001}).status_code == 422
    r = say(client, "x" * 3000)
    assert kinds(r)[0] == "text_too_long" and "too long" in r["messages"][0]


def test_a_state_the_server_produced_is_always_accepted_back(client):
    r = say(client, "I live in Pune")
    r = say(client, "I am 30 years old", r["state"])
    r = say(client, "sorry, my age is 35", r["state"])
    assert r["state"]["answers"]["age_years"] == [35, "explicit"]


# ------------------------------------------------------------------ privacy
def test_messages_are_never_logged_or_stored(client, caplog):
    secret = "ZXQ-secret-note-7731"
    with caplog.at_level(logging.DEBUG):
        r = say(client, f"I am 41 years old and my note is {secret}")
    assert secret not in caplog.text and secret not in json.dumps(r, ensure_ascii=False)


def test_the_server_keeps_no_conversation_between_calls(client):
    a = say(client, "I am 30 years old")
    b = say(client, "hello")                       # a different person: no state sent
    assert "age_years" not in b["state"]["answers"] and a["state"]["answers"]["age_years"] == [30, "explicit"]


# ------------------------------------------------------------------ interface strings
def test_ui_text_has_identical_keys_in_every_language(client):
    def keys(d, prefix=""):
        out = set()
        for k, v in d.items():
            out |= keys(v, f"{prefix}{k}.") if isinstance(v, dict) else {f"{prefix}{k}"}
        return out

    en = client.get("/api/v1/ui/en").json()
    for lang in ("hi", "mr"):
        other = client.get(f"/api/v1/ui/{lang}").json()
        assert keys(other["ui"]) == keys(en["ui"])
    assert client.get("/api/v1/ui/fr").status_code == 400


def test_fields_follow_the_language_and_refuse_unknown_ones(client):
    hi = {f["key"]: f for f in client.get("/api/v1/fields?lang=hi").json()["fields"]}
    en = {f["key"]: f for f in client.get("/api/v1/fields").json()["fields"]}
    assert hi["state_code"]["value_labels"]["MH"] == "महाराष्ट्र" and en["state_code"]["value_labels"]["MH"] == "Maharashtra"
    assert hi["age_years"]["label"] != en["age_years"]["label"] and set(hi) == set(en)
    assert client.get("/api/v1/fields?lang=fr").status_code == 400
