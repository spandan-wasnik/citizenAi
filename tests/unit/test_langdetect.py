import json
from pathlib import Path

import pytest

from app.nlp.langdetect import detect_language
from scripts.eval_language import evaluate

GOLD = json.loads((Path(__file__).resolve().parents[2] / "eval" / "language_gold.json").read_text(encoding="utf-8"))


def test_gold_accuracy_and_no_cross_language_mistakes():
    r = evaluate(GOLD)
    assert r["accuracy"] >= 0.95, r["wrong"]
    # the costly mistakes are answering in the wrong language, so none are tolerated:
    wrong_language = [w for w in r["wrong"] if w["got"] is not None and w["expected"] is not None]
    assert wrong_language == []


@pytest.mark.parametrize("text,lang,script", [
    ("I am a student", "en", "latin"), ("मैं छात्र हूँ", "hi", "devanagari"), ("मी विद्यार्थी आहे", "mr", "devanagari"),
    ("main kisan hoon", "hi", "latin"), ("mi shetkari ahe", "mr", "latin"),
])
def test_language_and_script(text, lang, script):
    d = detect_language(text)
    assert (d.language, d.script) == (lang, script)
    assert d.romanised == (script == "latin" and lang != "en")


@pytest.mark.parametrize("text", ["30", "२ लाख", "ok", "", "   ", "?!", "Maharashtra", "महाराष्ट्र"])
def test_no_evidence_means_unknown_not_a_guess(text):
    assert detect_language(text).language is None


def test_hindi_and_marathi_yes_no_are_told_apart():
    assert detect_language("नाही").language == "mr" and detect_language("नहीं").language == "hi"


def test_tie_in_mixed_text_is_unknown_and_reported():
    d = detect_language("I am 28, मैं महिला हूँ and मेरा income 2 lakh है")
    assert d.language is None and d.reason == "tie" and d.script == "mixed"


def test_one_stray_word_is_not_strong_enough_to_switch_language():
    assert not detect_language("yes").strong
    assert detect_language("I live in Maharashtra and I have land").strong
    assert detect_language("मैं महाराष्ट्र में रहती हूँ").strong


def test_shared_romanised_words_do_not_decide():
    assert detect_language("nahi").language is None


def test_evidence_is_exposed_for_explanation():
    d = detect_language("मी शेतकरी आहे")
    assert d.evidence["mr"] > 0 and d.evidence["hi"] == 0


def test_never_crashes_on_odd_input():
    for t in ["\u0000", "😀😀", "a" * 5000, "​", "१" * 100, "ऀ" * 10]:
        detect_language(t)
