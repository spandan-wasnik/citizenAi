"""Turn conversation actions into sentences in the person's language."""
from __future__ import annotations

from typing import Any

from app.conversation.state import Action
from app.domain.explain import format_inr
from app.domain.fields import FIELD_TYPES
from app.i18n.catalog import catalog, message

STATE_FIELDS = {"state_code", "domicile_state_code", "study_state_code"}


def label(field: str, lang: str) -> str:
    return catalog(lang)["labels"].get(field, field)


def format_value(field: str, value: Any, lang: str) -> str:
    c = catalog(lang)
    if isinstance(value, bool):
        return c["yes"] if value else c["no"]
    if field in STATE_FIELDS:
        return c["states"].get(str(value), str(value))
    if FIELD_TYPES.get(field) == "money_inr" and isinstance(value, int):
        return format_inr(value)
    if isinstance(value, str):
        return c["values"].get(field, {}).get(value, value.replace("_", " "))
    return str(value)


def render_action(a: Action, lang: str) -> str | None:
    d = a.data
    k = a.kind
    if k in ("greeting", "confirmed", "rejected_inferred", "skipped", "restarted", "show_results"):
        return message(lang, k)
    if k == "language_changed":
        return message(lang, k, language=catalog(lang)["languages"][d["language"]])
    if k == "text_too_long":
        return message(lang, k, limit=d["limit"])
    if k == "extracted":
        return message(lang, "noted", label=label(d["field"], lang), value=format_value(d["field"], d["value"], lang))
    if k == "updated":
        f = d["field"]
        return message(lang, k, label=label(f, lang), old=format_value(f, d["old"], lang), new=format_value(f, d["new"], lang))
    if k == "confirm_inferred":
        return message(lang, k, label=label(d["field"], lang), value=format_value(d["field"], d["value"], lang))
    if k == "conflict":
        return message(lang, k, label=label(d["field"], lang))
    if k == "ask_clarification":
        return message(lang, "income_whose", amount=format_inr(d["amount"]))
    if k == "nothing_understood":
        text = message(lang, k)
        return f"{text} {message(lang, 'skip_hint')}" if d.get("offer_skip") else text
    if k == "ask_field":
        if d.get("after_conflict"):
            return None                      # the conflict message already asks for the correct value
        f = d["field"]
        q = catalog(lang)["questions"].get(f) or f"{label(f, lang)}?"
        if d.get("sensitive"):
            return f"{q} {message(lang, 'optional_hint')}"
        return f"{q} {message(lang, 'skip_hint')}" if d.get("offer_skip") else q
    return None                              # extracted_inferred (the confirmation asks), answer_intent (answered elsewhere)


def render_turn(actions: list[Action], lang: str) -> list[str]:
    return [t for a in actions if (t := render_action(a, lang))]
