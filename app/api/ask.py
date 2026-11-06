"""Question answering over the verified records. Extractive: every answer is recorded text plus its source, or an honest
'not enough verified information'. No model is involved yet, and nothing the person types is stored or logged."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai.qa import answer
from app.ai.service import get_retriever
from app.api.errors import ApiError
from app.api.routes import _ERRORS, check_lang, get_session, get_today
from app.config import get_settings
from app.i18n.catalog import catalog, message
from app.nlp.intent import classify_intent, find_scheme_mentions
from app.nlp.langdetect import detect_language
from app.services.catalog_service import SchemeNotFound, get_scheme_detail

router = APIRouter(responses=_ERRORS)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(..., min_length=1, max_length=1000)
    language: str | None = Field(None, description="en, hi or mr. Detected from the question when omitted.")
    scheme_id: str | None = Field(None, max_length=100, description="Limit the answer to one scheme.")


def _lines(a, lang: str) -> list[str]:
    out = [message(lang, "ask_head")]
    for p in a.passages:
        tag = " " + message(lang, "english_note", language=catalog(lang)["languages"][lang]) if p["language"] != lang else ""
        src = p["sources"][0]["title"] if p["sources"] else ""
        out.append(f"{p['scheme_name']}: {p['text']}{tag}\n{message(lang, 'ask_source', title=src, locator=p['locator'] or '-')}")
    if a.confidence == "low":
        out.insert(1, message(lang, "ask_low"))
    out.append(message(lang, "ask_conf", level=message(lang, "conf_" + a.confidence)))
    if a.requires_verification:
        out.append(message(lang, "ask_verify"))
    return out


@router.post("/api/v1/ask")
def ask(body: AskRequest, request: Request, session: Session = Depends(get_session),
        today: date = Depends(get_today)) -> dict[str, Any]:
    lang = check_lang(body.language) if body.language else None
    if lang is None:
        d = detect_language(body.question[:2000])
        lang = d.language if d.language and d.strong else "en"
    intent = classify_intent(body.question[:2000])
    scope: tuple[str, ...] | None = None
    if body.scheme_id:
        try:
            get_scheme_detail(session, body.scheme_id, lang="en", today=today)
        except SchemeNotFound:
            raise ApiError(404, "scheme_not_found", "No scheme with that id is recorded.") from None
        scope = (body.scheme_id,)
    elif intent.scheme_ids:
        scope = tuple(intent.scheme_ids)
    retriever = get_retriever(request.app.state, session, today, get_settings())
    a = answer(retriever, body.question, language=lang, scheme_ids=scope, intent=intent.primary)
    links: list[dict[str, str]] = []
    if not a.found and scope and len(scope) == 1:
        det = get_scheme_detail(session, scope[0], lang=lang, today=today)
        links = [{"url": l["url"], "label": l["label"], "badge": l["badge"]} for l in det["links"] if l["domain_class"] != "suspicious"]
    messages = _lines(a, lang) if a.found else [message(lang, "ask_not_found")]
    return {"language": lang, "found": a.found, "messages": messages, "passages": a.passages, "sources": a.sources,
            "confidence": a.confidence, "requires_verification": a.requires_verification, "mode": a.mode,
            "scheme_ids": a.scheme_ids, "links": links}
