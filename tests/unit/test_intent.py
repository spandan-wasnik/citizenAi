import json
from pathlib import Path

import pytest

from app.nlp.intent import SCHEME_ALIASES, classify_intent, find_scheme_mentions
from app.services.scheme_loader import load_registry, load_scheme_dir

ROOT = Path(__file__).resolve().parents[2]
GOLD = json.loads((ROOT / "eval" / "intent_gold.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", GOLD, ids=[f"{i}-{c['text'][:25]}" for i, c in enumerate(GOLD)])
def test_gold(case):
    assert classify_intent(case["text"]).primary == case["intent"]


def test_scheme_aliases_point_only_at_real_schemes():
    reg = load_registry(ROOT / "data" / "sources" / "registry.yaml")
    schemes, errors = load_scheme_dir(ROOT / "data" / "schemes", reg)
    real = {s.scheme_id for s in schemes}
    assert set(SCHEME_ALIASES) <= real and real <= set(SCHEME_ALIASES), "every scheme needs aliases and none may be invented"


@pytest.mark.parametrize("text,ids", [
    ("Tell me about PM Kisan", ["pm-kisan"]), ("पीएम किसान के बारे में", ["pm-kisan"]), ("APY details", ["atal-pension-yojana"]),
    ("माझी लाडकी बहीण योजना", ["mukhyamantri-majhi-ladki-bahin"]), ("post-matric scholarship", ["post-matric-scholarship-sc"]),
    ("राजर्षी शाहू महाराज शुल्क", ["rajarshi-shahu-maharaj-ebc-fee-concession"]), ("nothing here", []),
    ("compare PM Kisan and Atal Pension", ["pm-kisan", "atal-pension-yojana"]),
])
def test_scheme_mentions(text, ids):
    assert sorted(find_scheme_mentions(text)) == sorted(ids)


def test_apy_alias_does_not_fire_inside_other_words():
    assert find_scheme_mentions("a happy day, therapy") == []


def test_scheme_is_reported_alongside_the_intent():
    i = classify_intent("What documents do I need for PM Kisan?")
    assert i.primary == "documents" and i.scheme_ids == ("pm-kisan",)


def test_two_requests_in_one_message_are_both_reported_in_priority_order():
    i = classify_intent("documents and how to apply for APY")
    assert i.all == ("documents", "how_to_apply")


@pytest.mark.parametrize("text,target", [("Please reply in Hindi", "hi"), ("मराठीत सांगा", "mr"), ("Switch to English", "en"),
                                          ("हिंदी में बताइए", "hi")])
def test_language_switch_target(text, target):
    i = classify_intent(text)
    assert i.primary == "language_switch" and i.target_language == target


def test_restart_beats_everything_and_evidence_is_kept():
    i = classify_intent("please start over, in marathi")
    assert i.primary == "restart" and "language_switch" in i.all and i.evidence["restart"] == "start over"


def test_greeting_only_counts_for_short_messages():
    assert classify_intent("hi").primary == "greeting"
    assert classify_intent("hi I am 30 years old and I live in Pune").primary == "other"


def test_odd_input_never_crashes():
    for t in ["", "\u0000", "😀", "a" * 5000, "?!", "१" * 300]:
        classify_intent(t)


def test_hindi_spellings_of_ladki_bahin_are_recognised_but_other_states_ladli_behna_is_not():
    from app.nlp.intent import find_scheme_mentions
    for q in ("लाडकी बहिन योजना", "लाडकी बहीण योजना", "लाडकी बहन योजना"):
        assert find_scheme_mentions(q) == ["mukhyamantri-majhi-ladki-bahin"], q
    assert find_scheme_mentions("लाडली बहना योजना मध्य प्रदेश") == []      # a different (Madhya Pradesh) scheme
    assert find_scheme_mentions("लाडली बहन योजना") == []
