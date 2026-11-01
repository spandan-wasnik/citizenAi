"""One error shape for the whole API: {"error": {"code", "message", "details"}}.

Validation errors NEVER echo the submitted values, because a request body may hold personal answers.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list | dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def body(code: str, message: str, details: list | dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"where": ".".join(str(p) for p in e.get("loc", ())), "problem": e.get("type", "invalid")}
                   for e in exc.errors()]  # deliberately no "input" and no "msg" (msg can quote the value)
        return JSONResponse(body("invalid_request", "The request could not be understood.", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(body(code, "No such resource." if exc.status_code == 404 else str(exc.detail)),
                            status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", extra={"path": request.url.path, "error_type": type(exc).__name__})
        return JSONResponse(body("internal_error", "Something went wrong on our side. Nothing was saved."),
                            status_code=500)
