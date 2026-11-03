"""Thin HTTP client for the CitizenAssist API.

The UI never touches the database or the rules itself: every fact it shows comes from the API,
so the browser app and any future client show the same answers. Tests replace `get_client`
with a FastAPI TestClient, which needs no running server.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
_client: httpx.Client | None = None


class ApiUnavailable(Exception):
    """The API could not be reached at all (not started, wrong address, network)."""


class ApiProblem(Exception):
    """The API answered with its structured error envelope."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status, self.code, self.message = status, code, message


def api_url() -> str:
    return os.environ.get("CITIZENASSIST_API_URL", DEFAULT_API_URL)


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=api_url(), timeout=20.0)
    return _client


def call(method: str, path: str, **kwargs: Any) -> Any:
    try:
        resp = get_client().request(method, path, **kwargs)
    except httpx.TransportError as e:
        raise ApiUnavailable(type(e).__name__) from None  # no URL or payload in the message
    if resp.status_code >= 400:
        try:
            err = resp.json()["error"]
            raise ApiProblem(resp.status_code, err["code"], err["message"])
        except (ValueError, KeyError, TypeError):
            raise ApiProblem(resp.status_code, "unexpected_response", "The server sent an unexpected reply.") from None
    return resp.json()
