"""The conversation engine: one message in, a new state and a list of actions out. Deterministic and side-effect free.

Per turn: detect the language, read the intent, read any facts (extractor), apply them to the state (explicit answers
override older ones, inferred ones wait for confirmation), then choose exactly one next step: resolve a conflict, ask
whose income it is, confirm an inferred value, ask the most useful unanswered question, or say the check is ready.
The most useful question is the one that the most schemes are still waiting for (taken from the eligibility engine's
decisive missing fields); sensitive questions come after ordinary ones and can always be skipped.
"""
from __future__ import annotations

import copy
from typing import Any

from app.conversation.evaluators import Evaluator
from app.conversation.state import Action, Answer, ConversationState
from app.domain.fields import FIELD_TYPES
from app.nlp.answers import parse_skip, parse_whose, parse_yes_no
from app.nlp.extract import MAX_TEXT, extract_profile
from app.nlp.intent import classify_intent
from app.nlp.langdetect import detect_language

INFO_INTENTS = {"documents", "how_to_apply", "official_link", "scheme_info", "help", "thanks"}
FIELD_ORDER = {f: i for i, f in enumerate(FIELD_TYPES)}
OFFER_SKIP_AFTER = 2


def next_question(results: list[dict[str, Any]], st: ConversationState) -> dict[str, Any] | None:
    """The unanswered question that most schemes are waiting for; None when nothing decisive is left."""
    have = set(st.profile()) | set(st.pending_confirm) | st.skipped
    scope = [r for r in results if not st.focus or r["scheme_id"] in st.focus]
    counts: dict[str, dict[str, Any]] = {}
    for r in scope:
        for mf in r["missing"]:
            if mf["field"] in have:
                continue
            e = counts.setdefault(mf["field"], {"field": mf["field"], "sensitive": mf["sensitive"], "schemes": 0})
            e["schemes"] += 1
    if not counts:
        return None
    return sorted(counts.values(), key=lambda e: (-e["schemes"], e["sensitive"], FIELD_ORDER.get(e["field"], 999)))[0]


def handle_message(state: ConversationState, text: str, evaluate: Evaluator) -> tuple[ConversationState, list[Action]]:
    if len(text) > MAX_TEXT:
        return state, [Action("text_too_long", {"limit": MAX_TEXT})]
    st = copy.deepcopy(state)
    st.turns += 1
    actions: list[Action] = []
    intent = classify_intent(text)
    det = detect_language(text)

    # ---- language
    new_lang = intent.target_language if intent.primary == "language_switch" else (det.language if det.strong else None)
    if new_lang and new_lang != st.language:
        st.language = new_lang
        actions.append(Action("language_changed", {"language": new_lang}))

    asked = st.expected_field
    touched: set[str] = set()
    conflict_fields: list[str] = []
    handled = False
    force_ask: str | None = None

    if intent.primary == "restart":
        lang = st.language
        st = ConversationState(language=lang, turns=st.turns)
        actions.append(Action("restarted"))
        handled, asked = True, None
    else:
        if intent.scheme_ids:
            st.focus = list(intent.scheme_ids)
        yn = parse_yes_no(text)
        if st.pending_confirm and yn is not None:
            f = st.pending_confirm.pop(0)
            if yn:
                st.answers[f].provenance = "confirmed"
                actions.append(Action("confirmed", {"field": f}))
            else:
                st.answers.pop(f, None)
                asked = force_ask = f
                actions.append(Action("rejected_inferred", {"field": f}))
            handled = True
        elif st.pending_income is not None and (who := parse_whose(text)):
            f = "annual_family_income" if who == "family" else "annual_individual_income"
            st.answers[f] = Answer(st.pending_income, "explicit")
            actions.append(Action("extracted", {"field": f, "value": st.pending_income}))
            st.pending_income = None
            handled = True
        elif asked and parse_skip(text):
            st.skipped.add(asked)
            st.expected_field = None
            actions.append(Action("skipped", {"field": asked}))
            handled = True

        if not handled:
            res = extract_profile(text, asked)
            for e in res.extractions:
                old = st.answers.get(e.field)
                if e.provenance == "explicit":
                    if old and old.provenance in ("explicit", "confirmed") and old.value != e.value:
                        actions.append(Action("updated", {"field": e.field, "old": old.value, "new": e.value}))
                    elif not old or old.provenance == "inferred":
                        actions.append(Action("extracted", {"field": e.field, "value": e.value}))
                    st.answers[e.field] = Answer(e.value, "explicit")
                    if e.field in st.pending_confirm:
                        st.pending_confirm.remove(e.field)
                    st.skipped.discard(e.field)
                else:
                    if old and (old.provenance in ("explicit", "confirmed") or old.value == e.value):
                        continue
                    st.answers[e.field] = Answer(e.value, "inferred")
                    if e.field not in st.pending_confirm:
                        st.pending_confirm.append(e.field)
                    actions.append(Action("extracted_inferred", {"field": e.field, "value": e.value, "note": e.note}))
                touched.add(e.field)
            for f in res.conflicts:
                conflict_fields.append(f)
                st.answers.pop(f, None)
                actions.append(Action("conflict", {"field": f}))
            for c in res.clarifications:
                if c.reason == "income_whose" and c.value is not None:
                    st.pending_income = c.value
                    actions.append(Action("ask_clarification", {"reason": "income_whose", "amount": c.value}))
            if asked and asked not in touched and not conflict_fields and not res.clarifications and not res.extractions \
                    and intent.primary not in INFO_INTENTS:
                st.fail_counts[asked] = st.fail_counts.get(asked, 0) + 1
                actions.append(Action("nothing_understood", {"field": asked,
                                                             "offer_skip": st.fail_counts[asked] >= OFFER_SKIP_AFTER}))
            if asked in touched:
                st.expected_field = None

    if intent.primary in INFO_INTENTS and not touched and not handled:
        actions.append(Action("answer_intent", {"intent": intent.primary, "scheme_ids": list(intent.scheme_ids)}))
        return st, actions
    if intent.primary == "greeting":
        actions.insert(0, Action("greeting"))

    # ---- exactly one next step
    if conflict_fields:
        st.expected_field = conflict_fields[0]
        actions.append(Action("ask_field", {"field": conflict_fields[0], "sensitive": False, "after_conflict": True}))
    elif st.pending_income is not None:
        if not any(a.kind == "ask_clarification" for a in actions):
            actions.append(Action("ask_clarification", {"reason": "income_whose", "amount": st.pending_income}))
        st.expected_field = None
    elif force_ask:
        st.expected_field = force_ask
        actions.append(Action("ask_field", {"field": force_ask, "sensitive": False, "after_rejection": True}))
    elif st.pending_confirm:
        f = st.pending_confirm[0]
        st.expected_field = None
        actions.append(Action("confirm_inferred", {"field": f, "value": st.answers[f].value}))
    else:
        q = next_question(evaluate(st.profile()), st)
        if q is None:
            st.expected_field = None
            actions.append(Action("show_results", {"profile_fields": sorted(st.profile())}))
        else:
            st.expected_field = q["field"]
            actions.append(Action("ask_field", {"field": q["field"], "sensitive": q["sensitive"],
                                                "offer_skip": st.fail_counts.get(q["field"], 0) >= OFFER_SKIP_AFTER or q["sensitive"]}))
    return st, actions
