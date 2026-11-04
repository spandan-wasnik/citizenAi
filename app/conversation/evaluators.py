"""Evaluators the conversation engine can use: they take a profile and report, per scheme, the status and the
decisive missing questions. The real one reads the schemes database; tests can supply any function of the same shape."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import models as m
from app.services.eligibility_service import evaluate_from_db

Evaluator = Callable[[dict[str, Any]], list[dict[str, Any]]]


def make_db_evaluator(session: Session, today: date) -> Evaluator:
    def evaluate(profile: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for sid in session.scalars(select(m.Scheme.scheme_id).order_by(m.Scheme.scheme_id)):
            a = evaluate_from_db(session, sid, profile, today=today)
            out.append({"scheme_id": sid, "status": a.status.value,
                        "missing": [{"field": x.field, "sensitive": x.sensitive} for x in a.missing_fields]})
        return out
    return evaluate
