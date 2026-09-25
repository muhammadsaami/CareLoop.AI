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
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    redis_url: str = ""

    # ─────────────────────────────────────────────────────────────────────────────
    # Phase 2 — Discharge Summary Ingestion, OCR & Structured Extraction
    # ─────────────────────────────────────────────────────────────────────────────

    # Document storage -------------------------------------------------------
    # Relative paths are resolved against the backend/ root directory.
    # Uploaded documents are NEVER stored inside the app/ Python package.
    document_storage_path: str = "storage/discharge_documents"

    # Upload limits ----------------------------------------------------------
    max_upload_size_mb: int = 10
    max_document_pages: int = 30

    # OCR --------------------------------------------------------------------
    # Full path to the Tesseract executable.  Leave empty to use the system
    # PATH.  Never hardcode a machine-specific installation path in code.
    tesseract_cmd: str = ""
    ocr_language: str = "eng"
    # Minimum characters of embedded text on a PDF page before it is treated
    # as a text page rather than a scanned page requiring OCR.
    ocr_min_chars_per_page: int = 40
    # Render DPI used when rasterising scanned PDF pages for OCR.
    pdf_ocr_render_dpi: int = 200

    # LLM providers ----------------------------------------------------------
    # Exactly one provider is used for extraction.  Only the SELECTED
    # provider's API key is required.
    llm_provider: str = "groq"

    # Default model names live here and ONLY here, so that a model name is
    # never hardcoded in more than one place.
    # NOTE: llama-3.3-70b-versatile was decommissioned by Groq and now
    # returns 404 model_not_found.  openai/gpt-oss-120b is a current
    # general-purpose model that supports json_schema structured output.
    default_groq_model: str = "openai/gpt-oss-120b"
    default_gemini_model: str = "gemini-2.0-flash"

    groq_api_key: str = ""
    groq_model: str = ""
    gemini_api_key: str = ""
    gemini_model: str = ""

    # Seconds to wait for an LLM extraction request before failing.
    llm_timeout_seconds: int = 60

    @property
    def groq_model_name(self) -> str:
        """Resolved Groq model name (config override, else single default)."""
        return self.groq_model or self.default_groq_model

    @property
    def gemini_model_name(self) -> str:
        """Resolved Gemini model name (config override, else single default)."""
        return self.gemini_model or self.default_gemini_model

    @property
    def max_upload_size_bytes(self) -> int:
        """Maximum permitted upload size in bytes."""
        return self.max_upload_size_mb * 1024 * 1024

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
