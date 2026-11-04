import re
from datetime import date
from pathlib import Path

import pytest

from app.conversation.engine import handle_message
from app.conversation.evaluators import make_db_evaluator
from app.conversation.state import Action, ConversationState
from app.database.connection import make_engine, session_scope
from app.domain.fields import FIELD_TYPES, PROFILE_FIELDS, STATE_CODES, STATE_NAMES
from app.i18n.catalog import LANGS, PROTECTED_TERMS, CatalogError, catalog, message, placeholders
from app.i18n.render import format_value, render_action, render_turn
from app.nlp.answers import parse_skip, parse_yes_no
from app.nlp.langdetect import detect_language
from app.services.scheme_loader import load_registry, load_scheme_dir, seed_schemes
from scripts.init_db import init_database

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_VALUES = {k: v for k, t, v, s, d in PROFILE_FIELDS if v}


# ------------------------------------------------------------------ catalogue integrity
@pytest.mark.parametrize("section", ["messages", "questions", "labels", "values", "states", "languages"])
def test_all_languages_have_identical_keys(section):
    keys = {lang: set(catalog(lang)[section]) for lang in LANGS}
    assert keys["hi"] == keys["en"] == keys["mr"], {l: keys[l] ^ keys["en"] for l in LANGS}


def test_placeholders_match_across_languages():
    for key, en in catalog("en")["messages"].items():
        for lang in ("hi", "mr"):
            assert placeholders(catalog(lang)["messages"][key]) == placeholders(en), (lang, key)


@pytest.mark.parametrize("lang", LANGS)
def test_no_empty_strings_and_no_stray_braces(lang):
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from walk(v)
        else:
            yield x
    for s in walk(catalog(lang)):
        assert isinstance(s, str) and s.strip()
    for key, t in catalog(lang)["questions"].items():
        assert "{" not in t, key


@pytest.mark.parametrize("lang", LANGS)
def test_every_state_and_every_field_has_a_name(lang):
    c = catalog(lang)
    assert set(c["states"]) == set(STATE_CODES) == set(STATE_NAMES)
    assert set(c["labels"]) == set(FIELD_TYPES)
    assert set(c["questions"]) <= set(FIELD_TYPES)
    for field, vals in c["values"].items():
        assert field in ALLOWED_VALUES and set(vals) <= set(ALLOWED_VALUES[field]), field


def test_english_state_names_are_the_domain_names():
    assert catalog("en")["states"] == STATE_NAMES


def test_questions_exist_for_every_field_the_engine_asks_about(tmp_path):
    # every field used by some scheme rule must be askable in all three languages
    url = f"sqlite:///{tmp_path / 'i.db'}"
    init_database(url)
    reg = load_registry(ROOT / "data" / "sources" / "registry.yaml")
    schemes, _ = load_scheme_dir(ROOT / "data" / "schemes", reg)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, schemes, reg)
        from sqlalchemy import select
        from app.database import models as m
        used = set(s.scalars(select(m.Rule.field_key)))
    eng.dispose()
    for lang in LANGS:
        assert used <= set(catalog(lang)["questions"]), (lang, used - set(catalog(lang)["questions"]))


@pytest.mark.parametrize("lang", LANGS)
def test_protected_category_names_stay_as_written(lang):
    q = catalog(lang)["questions"]["social_category"]
    assert all(t in q for t in PROTECTED_TERMS)


def test_unknown_language_is_refused_not_replaced():
    with pytest.raises(CatalogError):
        catalog("ta")
    with pytest.raises(CatalogError):
        message("en", "no_such_key")


# ------------------------------------------------------------------ the words we print are the words we understand
@pytest.mark.parametrize("lang", LANGS)
def test_yes_no_words_we_print_are_understood(lang):
    c = catalog(lang)
    assert parse_yes_no(c["yes"]) is True and parse_yes_no(c["no"]) is False


@pytest.mark.parametrize("lang", LANGS)
def test_the_skip_word_we_suggest_is_understood(lang):
    for key in ("skip_hint", "optional_hint"):
        quoted = re.search(r'"([^"]+)"', catalog(lang)["messages"][key]).group(1)
        assert parse_skip(quoted), (lang, quoted)


@pytest.mark.parametrize("lang", LANGS)
def test_state_names_we_print_are_understood_when_typed_back(lang):
    from app.nlp.extract import extract_profile
    for code, name in catalog(lang)["states"].items():          # all 36, in each language
        r = extract_profile(name, "state_code")
        assert r.get("state_code") and r.get("state_code").value == code, (lang, code, name)


def test_state_names_containing_and_are_not_cut_in_the_middle():
    from app.nlp.extract import extract_profile
    assert extract_profile("I live in Jammu and Kashmir and I do not own land").as_profile() == \
        {"state_code": "JK", "owns_agricultural_land": False}
    assert extract_profile("मैं जम्मू और कश्मीर में रहता हूँ और मेरे पास जमीन नहीं है").as_profile() == \
        {"state_code": "JK", "owns_agricultural_land": False}


# ------------------------------------------------------------------ value formatting
def test_value_formatting():
    assert format_value("annual_family_income", 250000, "hi") == "₹2,50,000"
    assert format_value("state_code", "MH", "mr") == "महाराष्ट्र"
    assert format_value("has_bank_account", True, "mr") == "होय" and format_value("has_bank_account", False, "hi") == "नहीं"
    assert format_value("social_category", "general", "mr") == "खुला" and format_value("social_category", "sc", "hi") == "SC"
    assert format_value("occupation", "farmer", "hi") == "किसान" and format_value("occupation", "farmer", "mr") == "शेतकरी"
    assert format_value("age_years", 30, "en") == "30"


# ------------------------------------------------------------------ rendering
@pytest.mark.parametrize("lang", LANGS)
def test_every_action_kind_renders_without_leftover_braces(lang):
    actions = [Action("greeting"), Action("extracted", {"field": "age_years", "value": 30}),
               Action("updated", {"field": "age_years", "old": 30, "new": 35}),
               Action("confirm_inferred", {"field": "state_code", "value": "MH"}), Action("confirmed", {"field": "state_code"}),
               Action("rejected_inferred", {"field": "state_code"}), Action("conflict", {"field": "age_years"}),
               Action("ask_clarification", {"reason": "income_whose", "amount": 300000}), Action("skipped", {"field": "gender"}),
               Action("nothing_understood", {"field": "age_years", "offer_skip": True}), Action("restarted"),
               Action("language_changed", {"language": "mr"}), Action("text_too_long", {"limit": 2000}),
               Action("ask_field", {"field": "gender", "sensitive": True, "offer_skip": True}),
               Action("ask_field", {"field": "age_years", "sensitive": False, "offer_skip": False}),
               Action("show_results", {})]
    for a in actions:
        t = render_action(a, lang)
        assert t and "{" not in t and "}" not in t, (lang, a.kind, t)


def test_actions_without_text_render_nothing():
    assert render_action(Action("answer_intent", {"intent": "documents", "scheme_ids": []}), "en") is None
    assert render_action(Action("extracted_inferred", {"field": "state_code", "value": "MH", "note": "x"}), "en") is None
    assert render_action(Action("ask_field", {"field": "age_years", "after_conflict": True}), "en") is None


def test_sensitive_question_says_it_is_optional_and_a_plain_one_does_not():
    assert "optional" in render_action(Action("ask_field", {"field": "gender", "sensitive": True}), "en")
    assert "optional" not in render_action(Action("ask_field", {"field": "age_years", "sensitive": False}), "en")


@pytest.fixture(scope="module")
def evaluate(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('i18n') / 'c.db'}"
    init_database(url)
    reg = load_registry(ROOT / "data" / "sources" / "registry.yaml")
    schemes, _ = load_scheme_dir(ROOT / "data" / "schemes", reg)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, schemes, reg)
    with session_scope(eng) as s:
        yield make_db_evaluator(s, date(2026, 11, 4))
    eng.dispose()


@pytest.mark.parametrize("lang,opening", [("hi", "मैं ३० साल की महिला हूँ और मैं पुणे में रहती हूँ"),
                                          ("mr", "मी ३० वर्षांची आहे आणि मी पुण्यात राहते")])
def test_hindi_and_marathi_turns_contain_no_english(evaluate, lang, opening):
    allowed = re.compile(r"SC|ST|OBC|EWS")
    st, acts = handle_message(ConversationState(), opening, evaluate)
    texts = render_turn(acts, st.language)
    for reply in ("हाँ" if lang == "hi" else "होय", "छोड़ो" if lang == "hi" else "सोडा"):
        st, acts = handle_message(st, reply, evaluate)
        texts += render_turn(acts, st.language)
    assert st.language == lang and len(texts) >= 4
    for t in texts:
        assert not re.search(r"[A-Za-z]", allowed.sub("", t)), t


def test_reply_language_is_detected_back_as_the_language_it_was_written_in(evaluate):
    for lang in LANGS:
        for key in ("confirm_inferred", "conflict", "skipped"):
            text = message(lang, key, label="x", value="y") if key != "skipped" else message(lang, key)
            assert detect_language(text).language in (lang, None)
