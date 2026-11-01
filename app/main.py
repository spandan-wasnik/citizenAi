"""FastAPI application factory.

    uvicorn app.main:create_app --factory --reload
Guest-first: no login is needed for any endpoint, and no answers are stored or logged.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.api import errors
from app.api.routes import API_VERSION, get_today, router
from app.core.logging_config import setup_logging
from app.config import get_settings
from app.database.connection import make_engine


def create_app(database_url: str | None = None, today_provider=None) -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="CitizenAssist AI", version=API_VERSION,
                  description="Preliminary scheme and eligibility information from official sources. "
                              "Not an official decision.")
    app.state.engine = make_engine(database_url or settings.database_url)
    if today_provider is not None:
        app.dependency_overrides[get_today] = today_provider
    errors.install(app)
    app.include_router(router)
    return app
