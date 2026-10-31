import io
import json
import logging

import pytest

from app.core.logging_config import redact, setup_logging

FAKE_KEY = "sk-" + "A1b2C3d4" * 4  # built at runtime so no key-like text sits in this file


@pytest.mark.parametrize("raw", [
    "1234 5678 9012", "1234-5678-9012", "123456789012", "१२३४ ५६७८ ९०१२",
])
def test_id_numbers_are_redacted(raw):
    out = redact(f"id {raw} end")
    assert "[REDACTED:ID_NUMBER]" in out
    assert "5678" not in out and "५६७८" not in out


@pytest.mark.parametrize("raw", ["+91 98765 43210", "9876543210", "98765-43210", "+919876543210"])
def test_phone_numbers_are_redacted(raw):
    out = redact(f"call {raw} now")
    assert "REDACTED" in out
    assert "98765" not in out and "43210" not in out


def test_email_pan_key_bearer_redacted():
    out = redact(f"a.b@example.com ABCDE1234F {FAKE_KEY} Bearer abcdef123456")
    for leaked in ("a.b@example.com", "ABCDE1234F", FAKE_KEY, "abcdef123456"):
        assert leaked not in out
    for kind in ("EMAIL", "PAN", "API_KEY", "BEARER"):
        assert f"[REDACTED:{kind}]" in out


@pytest.mark.parametrize("text", [
    "I am 21 years old and my family income is 1.5 lakh",
    "मला शिष्यवृत्ती हवी आहे, उत्पन्न 1.5 लाख",
    "Scheme PM-KISAN pays Rs 6000 per year",
    "Application deadline 2026-03-31",
])
def test_ordinary_text_is_left_alone(text):
    assert redact(text) == text


@pytest.fixture
def log_stream():
    stream = io.StringIO()
    setup_logging("DEBUG", stream)
    yield stream
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_citizenassist", False):
            root.removeHandler(h)


def lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_message_args_and_extras_never_leak(log_stream):
    log = logging.getLogger("test.pii")
    log.info("lookup for %s", "1234 5678 9012",
             extra={"annual_family_income": 150000, "district": "Pune",
                    "note": "reach me at a@b.co", "scheme_id": "pm-kisan"})
    raw = log_stream.getvalue()
    for leaked in ("1234 5678 9012", "150000", "Pune", "a@b.co"):
        assert leaked not in raw
    rec = lines(log_stream)[0]
    assert rec["scheme_id"] == "pm-kisan"  # non-sensitive structured data survives
    assert rec["level"] == "INFO"


def test_nested_structures_are_redacted(log_stream):
    logging.getLogger("test.nested").warning(
        "profile", extra={"profile": {"age": 21, "state": "MH", "contacts": ["x@y.in"]}})
    raw = log_stream.getvalue()
    assert "x@y.in" not in raw
    rec = lines(log_stream)[0]
    assert rec["profile"]["age"] == "[REDACTED]"
    assert rec["profile"]["state"] == "MH"


def test_exception_text_is_redacted(log_stream):
    try:
        raise ValueError("bad value for user@example.org")
    except ValueError:
        logging.getLogger("test.exc").exception("failed")
    raw = log_stream.getvalue()
    assert "user@example.org" not in raw
    assert "ValueError" in raw


def test_secret_like_keys_are_redacted(log_stream):
    logging.getLogger("test.keys").info("cfg", extra={"openai_api_key": "whatever", "Authorization": "x"})
    rec = lines(log_stream)[0]
    assert rec["openai_api_key"] == "[REDACTED]"
    assert rec["Authorization"] == "[REDACTED]"


def test_devanagari_is_preserved_and_output_is_valid_json(log_stream):
    logging.getLogger("test.i18n").info("योजना: पीएम-किसान")
    rec = lines(log_stream)[0]
    assert rec["message"] == "योजना: पीएम-किसान"


def test_setup_is_idempotent(log_stream):
    setup_logging("INFO", log_stream)
    setup_logging("INFO", log_stream)
    logging.getLogger("test.once").info("only once")
    assert len(lines(log_stream)) == 1
