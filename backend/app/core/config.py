"""
CareLoop AI — Core Configuration
Loads all settings from environment variables via pydantic-settings.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ─────────────────────────────────────────────────────────
    app_name: str = "CareLoop AI"
    environment: str = "development"

    # ── Database ─────────────────────────────────────────────────────────────
    # REQUIRED — supplied via environment configuration (process env or .env).
    # No code-level default exists so a missing value fails fast at startup
    # instead of silently connecting to an unintended database.
    database_url: str

    # ── Security ─────────────────────────────────────────────────────────────
    # REQUIRED — supplied via environment configuration.  Never hardcoded.
    secret_key: str

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: List[str] | str = ["http://localhost:3000", "http://localhost:5173"]

    @field_validator("cors_origins")
    @classmethod
    def parse_cors_origins(cls, value: str | List[str]) -> List[str]:
        """Accept either a Python list or a comma-separated string from .env."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # ── Future-phase stubs (not used in Phase 1) ─────────────────────────────
    groq_api_key: str = ""
    gemini_api_key: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    redis_url: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment.lower() == "testing"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
