import json
import logging
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.embeddings import HashingEmbedder
from app.database.connection import make_engine, session_scope
from app.main import create_app
from app.services.scheme_loader import load_registry, load_scheme_dir, seed_schemes
from scripts.init_db import init_database

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "data" / "sources" / "registry.yaml")
SCHEMES, _ = load_scheme_dir(ROOT / "data" / "schemes", REGISTRY)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('ask') / 'a.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
    eng.dispose()
    with TestClient(create_app(url, today_provider=lambda: date(2026, 11, 5))) as c:
        yield c


def ask(client, q, **kw):
    r = client.post("/api/v1/ask", json={"question": q, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_documents_question_returns_recorded_text_with_source(client):
    r = ask(client, "What documents do I need for PM Kisan land records?")
    assert r["found"] and r["scheme_ids"] == ["pm-kisan"] and r["mode"] == "keyword_only"
    p = r["passages"][0]
    assert p["section"] == "documents" and p["sources"][0]["url"].startswith("https://pmkisan.gov.in") and p["locator"]
    assert "source" in " ".join(r["messages"]).lower() and r["requires_verification"] is True


def test_every_passage_is_verbatim_recorded_text(client):
    r = ask(client, "What is the age eligibility for Atal Pension Yojana")
    assert r["found"]
    bodies = {c.body for c in client.app.state.retriever[1].chunks.values()}
    assert all(p["text"] in bodies for p in r["passages"])


def test_unanswerable_question_says_so_and_never_returns_the_nearest_text(client):
    r = ask(client, "What is the capital of France and who won the cricket world cup")
    assert not r["found"] and r["passages"] == [] and r["sources"] == [] and r["confidence"] == "none"
    assert "enough verified information" in r["messages"][0]


def test_not_found_inside_a_named_scheme_offers_only_recorded_links(client):
    r = ask(client, "zebra giraffe telescope", scheme_id="pm-kisan")
    known = {l.url for s in SCHEMES for l in s.links}
    assert not r["found"] and r["links"] and {l["url"] for l in r["links"]} <= known


def test_scheme_scope_is_respected(client):
    r = ask(client, "eligibility age income", scheme_id="atal-pension-yojana")
    assert {p["scheme_id"] for p in r["passages"]} <= {"atal-pension-yojana"}


def test_language_is_detected_or_chosen_and_answers_are_labelled(client):
    r = ask(client, "पीएम किसान के लिए दस्तावेज़ क्या चाहिए", language="hi")
    assert r["language"] == "hi"
    assert ask(client, "what documents for pm kisan")["language"] == "en"


def test_hindi_question_without_semantic_search_is_honest(client):
    r = ask(client, "किसानों को कितना पैसा मिलता है", language="hi")
    # Hindi words do not occur in the English records, so keyword search alone cannot answer: it must say so in Hindi
    assert r["mode"] == "keyword_only" and not r["found"] and "पर्याप्त" in r["messages"][0]


def test_bad_requests(client):
    assert client.post("/api/v1/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/v1/ask", json={"question": "x" * 1001}).status_code == 422
    assert client.post("/api/v1/ask", json={"question": "x", "extra": 1}).status_code == 422
    assert client.post("/api/v1/ask", json={"question": "x", "language": "fr"}).status_code == 400
    assert client.post("/api/v1/ask", json={"question": "x", "scheme_id": "nope"}).status_code == 404
    for q in ('"', "AND OR", "'; DROP TABLE schemes; --", "* ( ) ^"):
        assert client.post("/api/v1/ask", json={"question": q}).status_code == 200


def test_questions_are_never_logged(client, caplog):
    secret = "ZXQ-private-question-4417"
    with caplog.at_level(logging.DEBUG):
        r = ask(client, f"pension {secret}")
    assert secret not in caplog.text and secret not in json.dumps(r, ensure_ascii=False)


def test_semantic_mode_fuses_with_keywords_when_an_embedder_is_present(tmp_path):
    url = f"sqlite:///{tmp_path / 's.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
    eng.dispose()
    app = create_app(url, today_provider=lambda: date(2026, 11, 5))
    app.state.embedder = HashingEmbedder()               # test stand-in: word overlap only, not meaning
    with TestClient(app) as c:
        r = ask(c, "documents land records pm kisan")
    assert r["mode"] == "hybrid" and r["found"] and r["scheme_ids"] == ["pm-kisan"]


def test_naming_a_scheme_is_not_evidence_for_an_unrelated_question(client):
    r = ask(client, "what is the helpline colour of Ladki Bahin elephants")
    assert not r["found"]


def test_confidence_is_not_high_just_because_the_scheme_is_named(client):
    r = ask(client, "who can apply for Ladki Bahin")
    assert r["confidence"] in ("medium", "low", "high") and r["found"]
    r2 = ask(client, "Ladki Bahin")
    assert r2["found"] and {p["section"] for p in r2["passages"]} <= {"overview", "benefits", "who_for"}


def test_a_detail_the_records_do_not_hold_gets_no_answer_rather_than_a_guess(client):
    # the records hold no monthly pension amounts; nearby text about age must not be presented as the answer
    assert not ask(client, "monthly pension amount after age 60 Atal Pension Yojana")["found"]


def test_gold_file_is_consistent_with_the_records():
    gold = json.loads((ROOT / "eval" / "retrieval_gold.json").read_text(encoding="utf-8"))["cases"]
    ids = {s.scheme_id for s in SCHEMES}
    sections = {"overview", "benefits", "who_for", "eligibility", "condition", "documents", "steps"}
    for c in gold:
        assert c["lang"] in ("en", "hi", "mr") and c["q"].strip()
        if not c.get("none"):
            assert c["scheme"] in ids and set(c["sections"]) <= sections


def test_english_gold_questions_meet_a_floor_in_keyword_mode(client):
    """A regression floor, not a quality claim: the gold set is developer-written and optimistic."""
    gold = [c for c in json.loads((ROOT / "eval" / "retrieval_gold.json").read_text(encoding="utf-8"))["cases"] if c["lang"] == "en"]
    wrong = [c["q"] for c in gold if c.get("none") and ask(client, c["q"])["found"]]
    assert wrong == [], f"answered what the records cannot answer: {wrong}"


def test_low_confidence_answers_say_they_may_not_answer_the_question():
    from app.ai.qa import Answer
    from app.api.ask import _lines
    passage = {"scheme_name": "S", "text": "t", "language": "en", "locator": "p1", "sources": [{"url": "https://x.gov.in", "title": "T"}]}
    for lang, word in (("en", "closest"), ("hi", "नज़दीकी"), ("mr", "जवळचे")):
        low = _lines(Answer(found=True, passages=[passage], confidence="low"), lang)
        high = _lines(Answer(found=True, passages=[passage], confidence="high", requires_verification=False), lang)
        assert word in low[1] and not any(word in x for x in high)
