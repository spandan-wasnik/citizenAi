import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("APP_ENV", "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL",
                "OLLAMA_MODEL", "JWT_SECRET"):
        monkeypatch.delenv(var, raising=False)


def make(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)  # never read a developer's real .env in tests


def test_defaults_are_safe():
    s = make()
    assert s.app_env == "development"
    assert s.llm_provider == "none"
    assert s.effective_llm_provider == "none"
    assert s.llm_problem() is None


def test_openai_without_key_falls_back_and_explains():
    s = make(llm_provider="openai")
    assert s.effective_llm_provider == "none"
    assert "OPENAI_API_KEY" in s.llm_problem()


def test_openai_with_key_but_no_model_falls_back():
    s = make(llm_provider="openai", openai_api_key="k" * 20)
    assert s.effective_llm_provider == "none"
    assert "OPENAI_MODEL" in s.llm_problem()


def test_openai_fully_configured_is_used():
    s = make(llm_provider="openai", openai_api_key="k" * 20, openai_model="some-model")
    assert s.effective_llm_provider == "openai"
    assert s.llm_problem() is None


def test_blank_values_from_env_example_count_as_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "")
    s = make()
    assert s.effective_llm_provider == "none"


def test_ollama_needs_a_model():
    assert make(llm_provider="ollama").effective_llm_provider == "none"
    assert make(llm_provider="ollama", ollama_model="m").effective_llm_provider == "ollama"


def test_secret_never_appears_in_repr():
    secret = "sk-" + "z" * 30
    s = make(openai_api_key=secret, jwt_secret="supersecretvalue")
    text = repr(s) + str(s)
    assert secret not in text
    assert "supersecretvalue" not in text


def test_production_requires_jwt_secret():
    with pytest.raises(ValidationError):
        make(app_env="production")
    assert make(app_env="production", jwt_secret="x" * 32).app_env == "production"


def test_invalid_provider_rejected():
    with pytest.raises(ValidationError):
        make(llm_provider="skynet")
