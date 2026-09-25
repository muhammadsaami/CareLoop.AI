"""
CareLoop AI — Gemini LLM Provider (Phase 2)

Implements the LLMProvider interface using google-genai with structured
JSON output (`response_mime_type="application/json"` plus
`response_schema`).

Error translation:
  401/403 style auth failures -> ProviderAuthenticationError
  429                         -> ProviderRateLimitError
  timeouts / deadline         -> ProviderTimeoutError
  other API errors            -> ProviderError

The API key is read from settings and is never logged.
"""
from __future__ import annotations

from typing import Any, Optional

from app.core.config import Settings
from app.core.exceptions import (
    MissingAPIKeyError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.core.logging import get_logger
from app.llm.base import LLMProvider, parse_json_payload

logger = get_logger(__name__)

# Error names google-genai raises for transport-level problems.
_TIMEOUT_ERROR_NAMES = frozenset(
    {
        "DeadlineExceeded",
        "TimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
    }
)

_AUTH_ERROR_NAMES = frozenset(
    {
        "Unauthenticated",
        "PermissionDenied",
        "UnauthenticatedError",
    }
)

_RATE_LIMIT_ERROR_NAMES = frozenset(
    {
        "ResourceExhausted",
        "ResourceExhaustedError",
        "TooManyRequests",
    }
)


class GeminiProvider(LLMProvider):
    """Structured extraction via Google Gemini."""

    name = "gemini"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        super().__init__(settings)
        self._client = None

    # ── Configuration ───────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        """Resolved model name; configured once, in Settings."""
        return self._settings.gemini_model_name

    def is_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    def ensure_api_key(self) -> str:
        if not self._settings.gemini_api_key:
            raise MissingAPIKeyError(
                "GEMINI_API_KEY is not set. Configure it to run extraction "
                "with the Gemini provider.",
                internal_detail="gemini api key missing",
            )
        return self._settings.gemini_api_key

    def _get_client(self):
        """Lazily construct the Gemini client (imported on first use)."""
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - dependency present
                raise ProviderError(
                    "The Gemini SDK is not installed.",
                    internal_detail="google-genai import failed",
                ) from exc
            self._client = genai.Client(
                api_key=self.ensure_api_key(),
                http_options={"timeout": int(self._timeout(self._settings)) * 1000},
            )
        return self._client

    # ── Extraction ──────────────────────────────────────────────────────────

    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        self.ensure_configured()
        self.ensure_api_key()

        from google.genai import types

        config_kwargs: dict[str, Any] = {
            "temperature": 0,
            "max_output_tokens": 4096,
            "system_instruction": system_prompt,
            # Ask for JSON explicitly; Gemini's structured output also
            # accepts a schema, which is passed when provided.
            "response_mime_type": "application/json",
        }
        if json_schema:
            config_kwargs["response_schema"] = json_schema

        try:
            client = self._get_client()
            response = client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            content = getattr(response, "text", None)
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

        return parse_json_payload(content)

    # ── Error translation ───────────────────────────────────────────────────

    def _translate(self, exc: Exception) -> Exception:
        """Map a google-genai exception onto a CareLoop domain exception."""
        name = type(exc).__name__

        if name in _TIMEOUT_ERROR_NAMES:
            logger.error("Gemini request timed out")
            return ProviderTimeoutError(
                "The extraction provider timed out. Retry the request.",
                internal_detail="gemini timeout",
            )
        if name in _AUTH_ERROR_NAMES:
            logger.error("Gemini rejected the configured API key")
            return ProviderAuthenticationError(
                "The extraction provider rejected its credentials. "
                "Check the configured API key.",
                internal_detail="gemini authentication error",
            )
        if name in _RATE_LIMIT_ERROR_NAMES:
            logger.warning("Gemini rate limit exceeded")
            return ProviderRateLimitError(
                "The extraction provider rate limit was exceeded. "
                "Retry later.",
                internal_detail="gemini rate limit",
            )

        # Fall back to inspecting the message for a 401/403/429 marker.
        text = str(exc).lower()
        if "401" in text or "403" in text or "api key" in text:
            logger.error("Gemini rejected the configured API key")
            return ProviderAuthenticationError(
                "The extraction provider rejected its credentials.",
                internal_detail="gemini auth error by message",
            )
        if "429" in text or "quota" in text or "rate limit" in text:
            logger.warning("Gemini rate limit exceeded")
            return ProviderRateLimitError(
                "The extraction provider rate limit was exceeded.",
                internal_detail="gemini rate limit by message",
            )
        if "timeout" in text or "deadline" in text:
            return ProviderTimeoutError(
                "The extraction provider timed out.",
                internal_detail="gemini timeout by message",
            )

        logger.error("Gemini API error: %s", name)
        return ProviderError(
            "The extraction provider returned an error.",
            internal_detail=f"gemini api error: {name}",
        )
