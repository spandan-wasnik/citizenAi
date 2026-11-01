"""HTTP routes. Thin: validation and serialisation only; all logic lives in app/domain and app/services."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import ApiError
from app.config import get_settings
from app.database import models as m
from app.domain.eligibility import Assessment, SENSITIVE_FIELDS, Status
from app.domain.explain import LABELS, PRELIMINARY_NOTICE, REASON_TEXT, STATUS_TEXT, explain_result
from app.domain.fields import PROFILE_FIELDS
from app.services.catalog_service import SUPPORTED_LANGS, get_scheme_detail, list_schemes
from app.services.eligibility_service import SchemeNotFound, evaluate_from_db

API_VERSION = "0.1.0"
IST = timezone(timedelta(hours=5, minutes=30))
KNOWN_FIELDS = {f[0] for f in PROFILE_FIELDS}
MAX_PROFILE_KEYS, MAX_TEXT = 30, 100
STATUS_ORDER = {Status.LIKELY_ELIGIBLE: 0, Status.INSUFFICIENT_INFORMATION: 1,
                Status.REQUIRES_OFFICIAL_VERIFICATION: 2, Status.LIKELY_INELIGIBLE: 3}

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list | dict | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


# so the interactive docs show the error format the API really returns
_ERRORS = {code: {"model": ErrorEnvelope, "description": desc} for code, desc in
           {400: "Unsupported language or unavailable explanation language", 404: "Unknown scheme or route",
            422: "Request could not be used (values are never echoed back)", 500: "Unexpected error (generic message)",
            503: "Scheme database not ready"}.items()}

router = APIRouter(responses=_ERRORS)


# ----------------------------------------------------------------------------- dependencies
def get_today() -> date:
    return datetime.now(IST).date()


def get_session(request: Request) -> Iterator[Session]:
    engine = request.app.state.engine
    session = sessionmaker(engine, expire_on_commit=False)()
    try:
        # a cheap probe so a missing or uninitialised database becomes a clear 503, not a stack trace
        session.execute(select(func.count()).select_from(m.Scheme))
    except OperationalError as exc:
        session.close()
        raise ApiError(503, "database_not_ready",
                       "The scheme database is not ready. Run `python scripts/init_db.py` and "
                       "`python scripts/seed_database.py`.") from exc
    try:
        yield session
    finally:
        session.close()


def check_lang(lang: str) -> str:
    if lang not in SUPPORTED_LANGS:
        raise ApiError(400, "unsupported_language", "Supported languages are en, hi and mr.",
                       {"supported": list(SUPPORTED_LANGS)})
    return lang


def explanation_lang(lang: str) -> str:
    """Explanations exist in English only for now; never pretend otherwise."""
    if lang not in LABELS:
        raise ApiError(400, "language_not_available",
                       f"Eligibility explanations are not available in '{lang}' yet. Use lang=en.",
                       {"available": sorted(LABELS)})
    return lang


# ----------------------------------------------------------------------------- health
@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    settings = get_settings()
    info: dict[str, Any] = {"status": "ok", "version": API_VERSION, "environment": settings.app_env,
                            "llm_provider": settings.effective_llm_provider,
                            "llm_problem": settings.llm_problem()}
    try:
        with request.app.state.engine.connect() as conn:
            info["schemes"] = conn.execute(text("SELECT COUNT(*) FROM schemes")).scalar()
            info["database"] = "ok"
    except Exception:  # noqa: BLE001 - health must never raise
        info.update(status="degraded", database="not_ready", schemes=None)
    return info


# ----------------------------------------------------------------------------- catalogue
@router.get("/api/v1/fields")
def fields() -> dict[str, Any]:
    """The questions the evaluator understands, so a client can build a form that matches."""
    return {"fields": [{"key": k, "type": t, "allowed_values": sorted(v) if v else None,
                        "sensitive": bool(sens), "label": LABELS["en"].get(k, k), "description": d}
                       for k, t, v, sens, d in PROFILE_FIELDS]}


@router.get("/api/v1/schemes")
def schemes(lang: str = "en", level: str | None = Query(None, pattern="^(central|state|ut|local)$"),
            state: str | None = Query(None, pattern="^[A-Z]{2}$"), q: str | None = Query(None, max_length=100),
            tag: str | None = Query(None, max_length=40),
            verification_status: str | None = Query(None, pattern="^(draft|source_verified|human_reviewed)$"),
            session: Session = Depends(get_session)) -> dict[str, Any]:
    check_lang(lang)
    rows = list_schemes(session, lang=lang, level=level, state=state, verification_status=verification_status,
                        tag=tag, q=q)
    return {"count": len(rows), "schemes": rows}


@router.get("/api/v1/schemes/{scheme_id}")
def scheme_detail(scheme_id: str, lang: str = "en", session: Session = Depends(get_session),
                  today: date = Depends(get_today)) -> dict[str, Any]:
    check_lang(lang)
    try:
        return get_scheme_detail(session, scheme_id, lang=lang, today=today)
    except SchemeNotFound:
        raise ApiError(404, "scheme_not_found", "No scheme with that id.") from None


# ----------------------------------------------------------------------------- evaluation
class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: dict[str, str | int | float | bool | None]
    lang: str = "en"


def _check_profile(profile: dict[str, Any]) -> None:
    if len(profile) > MAX_PROFILE_KEYS:
        raise ApiError(422, "profile_too_large", f"At most {MAX_PROFILE_KEYS} answers per request.")
    unknown = sorted(k for k in profile if k not in KNOWN_FIELDS)
    if unknown:  # key names only; values are never echoed
        raise ApiError(422, "unknown_profile_field", "Some answers are not about a field we know.",
                       {"unknown_fields": unknown[:10], "known_fields_url": "/api/v1/fields"})
    if any(isinstance(v, str) and len(v) > MAX_TEXT for v in profile.values()):
        raise ApiError(422, "profile_value_too_long", f"Text answers can be at most {MAX_TEXT} characters.")


def serialise(a: Assessment, lang: str, record: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "scheme_id": a.scheme_id, "status": a.status.value, "headline": STATUS_TEXT[lang][a.status],
        "top_result": a.top.value,
        "reasons": [{"code": c, "text": REASON_TEXT[lang].get(c, c)} for c in a.reasons],
        "warnings": [{"code": c, "text": REASON_TEXT[lang].get(c, c)} for c in a.warnings],
        "rules": [{"field": r.rule.field, "label": LABELS[lang].get(r.rule.field, r.rule.field),
                   "purpose": r.purpose, "result": r.result.value, "reason_code": r.reason_code,
                   "explanation": explain_result(r, lang), "source_url": r.rule.source_url,
                   "locator": r.rule.locator} for r in a.results],
        "missing": [{"field": x.field, "label": LABELS[lang].get(x.field, x.field), "reason": x.reason,
                     "sensitive": x.sensitive} for x in a.missing_fields],
        "confirm_officially": [{"kind": c.kind, "text": c.texts[lang], "source_url": c.source_url,
                                "locator": c.locator} for c in a.other_conditions if lang in c.texts],
        "notice": PRELIMINARY_NOTICE[lang], "engine_version": a.engine_version, "rules_hash": a.rules_hash,
        "evaluated_on": a.evaluated_on.isoformat(), "record": record,
    }


def _record(session: Session, scheme_id: str, today: date) -> dict[str, Any]:
    s = session.get(m.Scheme, scheme_id)
    return {"verification_status": s.verification_status, "status": s.status, "rules_complete": bool(s.rules_complete),
            "last_verified": s.last_verified.isoformat() if s.last_verified else None,
            "next_review_due": s.next_review_due.isoformat() if s.next_review_due else None}


@router.post("/api/v1/schemes/{scheme_id}/evaluate")
def evaluate_one(scheme_id: str, body: EvaluateRequest, session: Session = Depends(get_session),
                 today: date = Depends(get_today)) -> dict[str, Any]:
    lang = explanation_lang(check_lang(body.lang))
    _check_profile(body.profile)
    try:
        a = evaluate_from_db(session, scheme_id, body.profile, today=today)
    except SchemeNotFound:
        raise ApiError(404, "scheme_not_found", "No scheme with that id.") from None
    return serialise(a, lang, _record(session, scheme_id, today))


@router.post("/api/v1/evaluate")
def evaluate_all(body: EvaluateRequest, session: Session = Depends(get_session),
                 today: date = Depends(get_today)) -> dict[str, Any]:
    """Check every scheme against one profile. Order: likely eligible, more information needed, needs verification,
    likely not eligible; within a group by scheme id, so the order is stable and explainable."""
    lang = explanation_lang(check_lang(body.lang))
    _check_profile(body.profile)
    names = {t.scheme_id: t.official_name for t in session.scalars(
        select(m.SchemeTranslation).where(m.SchemeTranslation.lang == "en"))}
    results = []
    for sid in session.scalars(select(m.Scheme.scheme_id).order_by(m.Scheme.scheme_id)):
        a = evaluate_from_db(session, sid, body.profile, today=today)
        item = serialise(a, lang, _record(session, sid, today))
        item["name"] = names.get(sid, sid)
        results.append((STATUS_ORDER[a.status], sid, item))
    results.sort(key=lambda t: (t[0], t[1]))
    counts: dict[str, int] = {}
    for _, _, item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"evaluated_on": today.isoformat(), "counts": counts, "results": [r[2] for r in results],
            "notice": PRELIMINARY_NOTICE[lang]}
