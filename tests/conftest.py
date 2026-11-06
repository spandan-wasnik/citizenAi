"""Tests must not depend on the developer's own .env. A real .env may switch on semantic search (which loads a 470 MB
model and changes answers) or hold an API key; neither may leak into a unit test. Environment variables win over
.env, so the settings that matter are pinned here."""
import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SEARCH", "false")
    monkeypatch.delenv("SEMANTIC_MIN_Z", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
