"""Structured JSON logging with PII redaction.

Two layers protect the log output:
  * ``RedactingFilter`` (attached to the handler) scrubs the message, its args and any
    structured ``extra`` fields before formatting.
  * ``JsonFormatter`` scrubs once more at the end, which also covers exception text.

Limits, stated plainly: free text is scrubbed for *identifiers* (ID numbers, phones,
emails, PAN, API keys, bearer tokens). Ordinary words and numbers in free text, such as
"I am 21", cannot be reliably detected, so code must not log user text at all. Structured
fields with sensitive names (income, age, district, ...) are redacted by key.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

_D = "0-9०-९"  # ASCII and Devanagari digits

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("API_KEY", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")),
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    # 12-digit ID numbers (Aadhaar-style), with optional spaces or hyphens between groups.
    ("ID_NUMBER", re.compile(rf"(?<![{_D}])[{_D}]{{4}}[ -]?[{_D}]{{4}}[ -]?[{_D}]{{4}}(?![{_D}])")),
    # Indian mobile numbers, optional +91, optional split after five digits.
    ("PHONE", re.compile(rf"(?<![{_D}])(?:\+?91[ -]?)?[6-9६-९][{_D}]{{4}}[ -]?[{_D}]{{5}}(?![{_D}])")),
]

SENSITIVE_KEYS = {
    "email", "phone", "mobile", "aadhaar", "aadhar", "pan", "income",
    "annual_family_income", "annual_individual_income", "age", "age_years",
    "gender", "social_category", "disability", "disability_status", "district",
    "name", "display_name", "dob", "date_of_birth", "address",
    "query", "user_text", "message_text",
}
_SENSITIVE_FRAGMENTS = ("password", "secret", "token", "api_key", "apikey", "authorization")

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def redact(text: str) -> str:
    """Replace identifier-like substrings with [REDACTED:KIND]."""
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_KEYS or any(f in lowered for f in _SENSITIVE_FRAGMENTS)


def redact_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_value(key, v) for v in value]
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        for key in [k for k in record.__dict__ if k not in _STANDARD_ATTRS]:
            record.__dict__[key] = redact_value(key, record.__dict__[key])
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact_value("payload", payload), ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Install one redacting JSON handler on the root logger (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_citizenassist", False):
            root.removeHandler(handler)

    target = stream or sys.stderr
    if hasattr(target, "reconfigure"):
        try:
            target.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass

    handler = logging.StreamHandler(target)
    handler._citizenassist = True  # type: ignore[attr-defined]
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
