"""Application settings, read from environment variables and an optional .env file.

Secrets are held as SecretStr so they never appear in repr() or logs by accident.
An incomplete LLM configuration does not crash the app: it falls back to "none"
(extractive mode) and reports the reason via ``llm_problem()``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LlmProvider = Literal["none", "openai", "ollama"]


def _has_value(secret: SecretStr | None) -> bool:
    return bool(secret and secret.get_secret_value().strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/citizenassist.db"
    jwt_secret: SecretStr | None = None

    llm_provider: LlmProvider = "none"
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None  # deliberately no default: we do not guess model names
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str | None = None

    embedding_model: str = "intfloat/multilingual-e5-small"
    guest_session_ttl_hours: int = 24

    @model_validator(mode="after")
    def _require_jwt_secret_in_production(self) -> "Settings":
        if self.app_env == "production" and not _has_value(self.jwt_secret):
            raise ValueError("JWT_SECRET must be set when APP_ENV=production")
        return self

    def llm_problem(self) -> str | None:
        """Why the configured LLM provider cannot be used, or None if it is usable."""
        if self.llm_provider == "openai":
            if not _has_value(self.openai_api_key):
                return "LLM_PROVIDER=openai but OPENAI_API_KEY is not set"
            if not (self.openai_model or "").strip():
                return "LLM_PROVIDER=openai but OPENAI_MODEL is not set"
        if self.llm_provider == "ollama" and not (self.ollama_model or "").strip():
            return "LLM_PROVIDER=ollama but OLLAMA_MODEL is not set"
        return None

    @property
    def effective_llm_provider(self) -> LlmProvider:
        return "none" if self.llm_problem() else self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
