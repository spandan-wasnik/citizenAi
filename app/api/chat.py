"""Chat endpoint. Stateless: the client keeps the conversation state and sends it back with every message, so the server
stores nothing about anyone (not the messages, not the answers). The state holds values only, never the person's words,
and is validated strictly because it comes from outside."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.api.routes import _ERRORS, check_lang, evaluate_everything, get_session, get_today
from app.conversation.engine import handle_message
from app.conversation.evaluators import make_db_evaluator
from app.conversation.state import Action, ConversationState
from app.i18n.catalog import catalog, message
from app.i18n.render import render_action
from app.services.catalog_service import SchemeNotFound, get_scheme_detail

router = APIRouter(responses=_ERRORS)
MAX_SCHEMES_PER_REPLY = 3


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field("", max_length=10000, description="What the person typed. Empty starts a conversation.")
    state: dict[str, Any] | None = Field(None, description="The `state` returned by the previous reply; omit to start.")
    language: str | None = Field(None, description="Chosen interface language (en, hi, mr). Overrides detection for this turn.")


def _note(lang: str, used: str | None) -> str:
    return " " + message(lang, "english_note", language=catalog(lang)["languages"][lang]) if used and used != lang else ""


def info_messages(detail: dict[str, Any], intent: str, lang: str) -> tuple[list[str], dict[str, Any]]:
    """Plain-text (markdown list) answer to documents / steps / link / about questions, straight from verified records.
    Every line carries its source locator; text we only hold in English is labelled as such; suspicious links are withheld."""
    tr = detail["translation"] or {}
    name = tr.get("name") or detail["scheme_id"]
    src = message(lang, "source_label")
    links = [l for l in detail["links"] if l["domain_class"] != "suspicious"]
    link_lines = [f"- {l['label']}: {l['url']} — {l['badge']}" for l in links]
    out: list[str] = []
    if intent == "documents":
        if not detail["documents"]:
            out.append(message(lang, "info_none_documents", scheme=name))
        else:
            kinds = catalog(lang)["doc_kind"]
            used = {d["language_used"] for d in detail["documents"]} - {lang}
            out.append(message(lang, "info_documents", scheme=name) + (_note(lang, "en") if used else ""))
            out.append("\n".join(f"- {d['name']} ({kinds.get(d['requirement_type'], d['requirement_type'])})"
                                 + (f" — {d['purpose_note']}" if d.get("purpose_note") else "") + f" — {src}: {d['locator']}"
                                 for d in detail["documents"]))
    elif intent == "how_to_apply":
        if not detail["steps"]:
            out.append(message(lang, "info_none_steps", scheme=name))
        else:
            out.append(message(lang, "info_steps", scheme=name) + _note(lang, detail["steps"][0]["language_used"]))
            out.append("\n".join(f"{s['number']}. {s['instruction']} — {src}: {s['locator']}" for s in detail["steps"]))
        out.append(message(lang, "info_links", scheme=name) + "\n" + "\n".join(link_lines) if link_lines
                   else message(lang, "info_none_links", scheme=name))
    elif intent == "official_link":
        out.append(message(lang, "info_links", scheme=name) + "\n" + "\n".join(link_lines) if link_lines
                   else message(lang, "info_none_links", scheme=name))
    else:  # scheme_info
        parts = [tr.get(k) for k in ("short_description", "benefits", "target_beneficiaries") if tr.get(k)]
        if parts:
            out.append(message(lang, "info_about", scheme=name) + _note(lang, tr.get("language_used")))
            out.append("\n\n".join(parts))
        else:
            out.append(message(lang, "info_none_about", scheme=name))
    payload = {"scheme_id": detail["scheme_id"], "name": name, "intent": intent,
               "links": [{"url": l["url"], "label": l["label"], "badge": l["badge"]} for l in links]}
    return out, payload


@router.post("/api/v1/chat")
def chat(body: ChatRequest, session: Session = Depends(get_session), today: date = Depends(get_today)) -> dict[str, Any]:
    """One turn of the guided conversation. Returns the new state, the reply as text in the person's language, the
    structured actions behind it, any scheme information asked for, and, when enough is known, the results."""
    if body.language is not None:
        check_lang(body.language)
    if body.state is None:
        state, starting = ConversationState(language=body.language or "en"), True
    else:
        try:
            state = ConversationState.from_untrusted(body.state)
        except ValueError:
            raise ApiError(422, "invalid_state", "The conversation state is not valid. Please start a new conversation.") from None
        starting = False
        if body.language:
            state.language = body.language

    new_state, actions = handle_message(state, body.message, make_db_evaluator(session, today))
    if starting and not body.message.strip() and not any(a.kind == "greeting" for a in actions):
        actions.insert(0, Action("greeting"))
    lang = new_state.language

    messages: list[str] = []
    infos: list[dict[str, Any]] = []
    for a in actions:
        if a.kind != "answer_intent":
            if (t := render_action(a, lang)):
                messages.append(t)
            continue
        intent = a.data["intent"]
        if intent == "help":
            messages.append(message(lang, "help_text"))
        elif intent == "thanks":
            messages.append(message(lang, "thanks_reply"))
        else:
            ids = (a.data["scheme_ids"] or new_state.focus)[:MAX_SCHEMES_PER_REPLY]
            if not ids:
                messages.append(message(lang, "which_scheme"))
            for sid in ids:
                try:
                    detail = get_scheme_detail(session, sid, lang=lang, today=today)
                except SchemeNotFound:
                    messages.append(message(lang, "which_scheme"))
                    continue
                text, payload = info_messages(detail, intent, lang)
                messages += text
                infos.append(payload)

    results = None
    if any(a.kind == "show_results" for a in actions):
        results = evaluate_everything(session, new_state.profile(), lang, today)
    return {"state": new_state.to_dict(), "language": lang, "messages": messages,
            "actions": [{"kind": a.kind, "data": a.data} for a in actions], "info": infos, "results": results,
            "done": results is not None}
