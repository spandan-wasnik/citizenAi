import json
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.conversation.engine import handle_message, next_question
from app.conversation.evaluators import make_db_evaluator
from app.conversation.state import ConversationState
from app.database.connection import make_engine, session_scope
from app.domain.fields import FIELD_TYPES
from app.services.scheme_loader import load_registry, load_scheme_dir, seed_schemes
from scripts.init_db import init_database

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "data" / "sources" / "registry.yaml")
SCHEMES, _ = load_scheme_dir(ROOT / "data" / "schemes", REGISTRY)


@pytest.fixture(scope="module")
def evaluate(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('conv') / 'c.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
    with session_scope(eng) as s:
        yield make_db_evaluator(s, date(2026, 11, 4))
    eng.dispose()


def run(evaluate, messages, state=None):
    state = state or ConversationState()
    log = []
    for m in messages:
        state, acts = handle_message(state, m, evaluate)
        log.append(acts)
    return state, log


def kinds(acts):
    return [a.kind for a in acts]


def last(acts, kind):
    return next(a for a in reversed(acts) if a.kind == kind)


# ------------------------------------------------------------------ asking
def test_first_question_is_the_one_most_schemes_wait_for(evaluate):
    st, log = run(evaluate, ["hello"])
    assert kinds(log[0]) == ["greeting", "ask_field"]
    assert last(log[0], "ask_field").data["field"] == "annual_family_income"      # needed by three of five schemes
    assert st.expected_field == "annual_family_income"


def test_volunteered_facts_are_echoed_and_not_asked_again(evaluate):
    st, log = run(evaluate, ["I am a 30 year old woman and my family income is 2 lakh"])
    got = {a.data["field"]: a.data["value"] for a in log[0] if a.kind == "extracted"}
    assert got == {"age_years": 30, "gender": "female", "annual_family_income": 200000}
    assert last(log[0], "ask_field").data["field"] not in st.profile()


def test_a_bare_answer_goes_to_the_question_just_asked(evaluate):
    st, log = run(evaluate, ["hello", "2 lakh"])
    assert st.profile() == {"annual_family_income": 200000}


def test_focus_on_one_scheme_asks_only_what_that_scheme_needs(evaluate):
    st, log = run(evaluate, ["Am I eligible for PM Kisan?"])
    assert last(log[0], "ask_field").data["field"] == "owns_agricultural_land"
    assert st.focus == ["pm-kisan"]


# ------------------------------------------------------------------ inferred values need confirmation
def test_inferred_value_is_not_used_until_confirmed(evaluate):
    st, log = run(evaluate, ["I live in Pune"])
    assert st.answers["state_code"].provenance == "inferred" and "state_code" not in st.profile()
    assert kinds(log[0])[-1] == "confirm_inferred"
    st, log = run(evaluate, ["yes"], st)
    assert st.profile()["state_code"] == "MH" and "confirmed" in kinds(log[0])


def test_rejected_inference_is_dropped_and_asked_directly(evaluate):
    st, log = run(evaluate, ["I live in Pune", "no"])
    assert "state_code" not in st.answers and st.expected_field == "state_code"
    assert "rejected_inferred" in kinds(log[1])


def test_explicit_statement_replaces_a_pending_inference(evaluate):
    st, _ = run(evaluate, ["I live in Pune", "I live in Maharashtra"])
    assert st.answers["state_code"].provenance == "explicit" and not st.pending_confirm


# ------------------------------------------------------------------ corrections and conflicts
def test_correction_overwrites_and_reports_the_change(evaluate):
    st, log = run(evaluate, ["I am 30 years old", "sorry, my age is 35"])
    assert st.profile()["age_years"] == 35
    assert last(log[1], "updated").data == {"field": "age_years", "old": 30, "new": 35}


def test_conflict_in_one_message_sets_nothing_and_asks(evaluate):
    st, log = run(evaluate, ["I am 30 years old. Actually my age is 35"])
    assert "age_years" not in st.answers and st.expected_field == "age_years"
    assert "conflict" in kinds(log[0])


# ------------------------------------------------------------------ income: whose?
def test_ambiguous_income_asks_whose_then_completes(evaluate):
    st, log = run(evaluate, ["income is 3 lakh"])
    assert last(log[0], "ask_clarification").data == {"reason": "income_whose", "amount": 300000}
    st, log = run(evaluate, ["family"], st)
    assert st.profile()["annual_family_income"] == 300000 and st.pending_income is None


def test_income_whose_in_marathi_and_hindi(evaluate):
    st, _ = run(evaluate, ["उत्पन्न दोन लाख", "कुटुंबाचे"])
    assert st.profile()["annual_family_income"] == 200000
    st, _ = run(evaluate, ["आय 3 लाख है", "मेरी"])
    assert st.profile()["annual_individual_income"] == 300000


# ------------------------------------------------------------------ skipping, restart, language
def test_skipped_question_is_never_asked_again(evaluate):
    st, log = run(evaluate, ["hello", "skip"])
    assert "annual_family_income" in st.skipped
    st, log2 = run(evaluate, ["hi", "hello"], st)
    for acts in log2:
        for a in acts:
            assert not (a.kind == "ask_field" and a.data["field"] == "annual_family_income")


def test_sensitive_questions_come_with_a_skip_offer(evaluate):
    st, log = run(evaluate, ["I am 30, I live in Maharashtra", "yes"])
    st = ConversationState()
    seen = {}
    for _ in range(12):
        st, acts = handle_message(st, "skip" if st.expected_field else "hello", evaluate)
        for a in acts:
            if a.kind == "ask_field":
                seen[a.data["field"]] = a.data
    assert seen["gender"]["sensitive"] and seen["gender"]["offer_skip"]


def test_restart_clears_answers_but_keeps_language(evaluate):
    st, _ = run(evaluate, ["मी ३० वर्षांची आहे आणि मी महाराष्ट्रात राहते"])
    assert st.language == "mr" and st.profile()
    st, log = run(evaluate, ["पुन्हा सुरू करा"], st)
    assert st.profile() == {} and st.language == "mr" and "restarted" in kinds(log[0])


def test_language_follows_strong_evidence_but_not_a_stray_word(evaluate):
    st, log = run(evaluate, ["मैं महाराष्ट्र में रहती हूँ"])
    assert st.language == "hi" and "language_changed" in kinds(log[0])
    st, log = run(evaluate, ["yes"], st)
    assert st.language == "hi"
    st, _ = run(evaluate, ["please reply in marathi"], st)
    assert st.language == "mr"


# ------------------------------------------------------------------ information questions
def test_information_questions_are_routed_and_do_not_disturb_the_pending_question(evaluate):
    st, log = run(evaluate, ["hello"])
    pending = st.expected_field
    st, log = run(evaluate, ["What documents do I need for PM Kisan?"], st)
    a = last(log[0], "answer_intent")
    assert a.data == {"intent": "documents", "scheme_ids": ["pm-kisan"]} and "ask_field" not in kinds(log[0])
    assert st.expected_field == pending


def test_not_understood_twice_offers_skip(evaluate):
    st, log = run(evaluate, ["hello", "banana", "banana"])
    assert last(log[1], "nothing_understood").data["offer_skip"] is False
    assert last(log[2], "nothing_understood").data["offer_skip"] is True


# ------------------------------------------------------------------ completion
OPENING = {"en": "I am a 30 year old woman and I live in Maharashtra",
           "hi": "मैं ३० साल की महिला हूँ और महाराष्ट्र में रहती हूँ",
           "mr": "मी ३० वर्षांची आहे आणि मी महाराष्ट्रात राहते"}
REPLIES = {  # what a person would answer to each question, per language
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


@pytest.mark.parametrize("lang", OPENING)
def test_full_conversation_finishes_without_repeating_a_question(evaluate, lang):
    st, acts = handle_message(ConversationState(), OPENING[lang], evaluate)
    asked = []
    for _ in range(15):
        if acts[-1].kind == "show_results":
            break
        q = last(acts, "ask_field").data["field"]
        asked.append(q)
        st, acts = handle_message(st, REPLIES[lang][q], evaluate)
    else:
        pytest.fail(f"no result; asked={asked}, profile={st.profile()}")
    assert len(asked) == len(set(asked)), asked
    assert set(asked).isdisjoint({"age_years", "state_code"})
    p = st.profile()
    assert p["age_years"] == 30 and p["gender"] == "female" and p["state_code"] == "MH" and p["annual_family_income"] == 250000
    assert p["owns_agricultural_land"] is False and p["social_category"] == "general" and p["domicile_state_code"] == "MH"
    assert st.language == lang


# ------------------------------------------------------------------ privacy and safety
def test_state_stores_values_not_the_persons_words(evaluate):
    st, _ = run(evaluate, ["I am a 30 year old woman and I live in Pune, my family income is 2.5 lakh"])
    dump = json.dumps(st.to_dict(), ensure_ascii=False).lower()
    for phrase in ("pune", "woman", "family income", "2.5 lakh", "years old"):
        assert phrase not in dump


def test_state_roundtrips_through_json(evaluate):
    st, _ = run(evaluate, ["I am 30 years old", "I live in Pune"])
    again = ConversationState.from_dict(json.loads(json.dumps(st.to_dict())))
    assert again == st


def test_overlong_text_is_refused_and_state_unchanged(evaluate):
    st = ConversationState()
    new, acts = handle_message(st, "a" * 2001, evaluate)
    assert new is st and kinds(acts) == ["text_too_long"]


def test_handle_message_does_not_mutate_the_state_it_was_given(evaluate):
    st = ConversationState()
    handle_message(st, "I am 30 years old", evaluate)
    assert st == ConversationState()


def test_next_question_orders_by_demand_then_non_sensitive_then_field_order():
    results = [{"scheme_id": "a", "missing": [{"field": "gender", "sensitive": True}, {"field": "age_years", "sensitive": False}]},
               {"scheme_id": "b", "missing": [{"field": "gender", "sensitive": True}, {"field": "state_code", "sensitive": False}]},
               {"scheme_id": "c", "missing": [{"field": "state_code", "sensitive": False}]}]
    assert next_question(results, ConversationState())["field"] == "state_code"   # 2 schemes, not sensitive, beats gender
    st = ConversationState(skipped={"state_code"})
    assert next_question(results, st)["field"] == "age_years" or next_question(results, st)["field"] == "gender"


@settings(max_examples=150, deadline=2000, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x97F), max_size=120), min_size=1, max_size=6))
def test_invariants_hold_for_any_conversation(evaluate, msgs):
    state = ConversationState()
    for m in msgs:
        state, acts = handle_message(state, m, evaluate)
        assert set(state.answers) <= set(FIELD_TYPES)
        assert state.expected_field is None or state.expected_field not in state.profile()
        assert all(state.answers[f].provenance == "inferred" for f in state.pending_confirm)
        assert acts and sum(a.kind in ("ask_field", "show_results", "confirm_inferred", "answer_intent") or a.kind == "ask_clarification" for a in acts) <= 2
