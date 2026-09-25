"""
CareLoop AI — Groq LLM Provider (Phase 2)

Implements the LLMProvider interface using Groq's OpenAI-compatible chat
completions API with JSON-schema structured output.

Error translation:
  AuthenticationError   -> ProviderAuthenticationError
  RateLimitError        -> ProviderRateLimitError
  APITimeoutError/Timeout-> ProviderTimeoutError
  BadRequestError       -> falls back to JSON-object mode, else ProviderError
  APIError              -> ProviderError

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

# Log a warning (not an error) if the provider falls back, since this is a
# deliberate compatibility measure rather than a failure.
_JSON_OBJECT_FALLBACK_LOGGED = False


class GroqProvider(LLMProvider):
    """Structured extraction via Groq."""

    name = "groq"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        super().__init__(settings)
        self._client = None
        self._supports_json_schema = True

    # ── Configuration ───────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        """Resolved model name; configured once, in Settings."""
        return self._settings.groq_model_name

    def is_configured(self) -> bool:
        return bool(self._settings.groq_api_key)

    def ensure_api_key(self) -> str:
        if not self._settings.groq_api_key:
            raise MissingAPIKeyError(
                "GROQ_API_KEY is not set. Configure it to run extraction "
                "with the Groq provider.",
                internal_detail="groq api key missing",
            )
        return self._settings.groq_api_key

    def _get_client(self):
        """Lazily construct the Groq client (imported on first use)."""
        if self._client is None:
            try:
                from groq import Groq
            except ImportError as exc:  # pragma: no cover - dependency present
                raise ProviderError(
                    "The Groq SDK is not installed.",
                    internal_detail="groq import failed",
                ) from exc
            self._client = Groq(
                api_key=self.ensure_api_key(),
                timeout=self._timeout(self._settings),
                max_retries=0,  # retries are handled by our own error mapping
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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            content = self._create_completion(
                messages=messages, json_schema=json_schema, schema_name=schema_name
            )
        except _FallbackToJsonObject:
            # Some models/deployments reject json_schema. Retry once in
            # plain JSON-object mode, which has broader support.
            global _JSON_OBJECT_FALLBACK_LOGGED
            if not _JSON_OBJECT_FALLBACK_LOGGED:
                logger.warning(
                    "Groq rejected json_schema for model=%s; "
                    "falling back to JSON object mode",
                    self.model,
                )
                _JSON_OBJECT_FALLBACK_LOGGED = True
            try:
                content = self._create_completion(
                    messages=messages, json_schema=None, schema_name=schema_name
                )
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

        return parse_json_payload(content)

    def _create_completion(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: Optional[dict[str, Any]],
        schema_name: str,
    ) -> str:
        """Issue one chat completion and return the message content."""
        client = self._get_client()

        if json_schema is not None:
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    # Best-effort mode: broader model support than
                    # strict mode, which is limited to a few models.
                    "strict": False,
                    "schema": json_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}

        completion = client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format=response_format,
            temperature=0,
            max_tokens=4096,
        )

        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise ProviderError(
                "The extraction provider returned no choices.",
                internal_detail="empty choices",
            )
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message else None
        if not content:
            raise ProviderError(
                "The extraction provider returned an empty response.",
                internal_detail="empty message content",
            )
        return content

    # ── Error translation ───────────────────────────────────────────────────

    def _translate(self, exc: Exception) -> Exception:
        """Map a Groq SDK exception onto a CareLoop domain exception."""
        try:
            import groq
        except ImportError:  # pragma: no cover - dependency present
            return ProviderError(
                "The extraction provider returned an error.",
                internal_detail=f"groq error: {exc.__class__.__name__}",
            )

        # Order matters: AuthenticationError/RateLimitError are subclasses of
        # APIStatusError, and APITimeoutError subclasses APIError.
        if isinstance(exc, groq.AuthenticationError):
            logger.error("Groq rejected the configured API key")
            return ProviderAuthenticationError(
                "The extraction provider rejected its credentials. "
                "Check the configured API key.",
                internal_detail="groq authentication error",
            )
        if isinstance(exc, groq.RateLimitError):
            logger.warning("Groq rate limit exceeded")
            return ProviderRateLimitError(
                "The extraction provider rate limit was exceeded. "
                "Retry later.",
                internal_detail="groq rate limit",
            )
        if isinstance(exc, (groq.APITimeoutError, groq.Timeout)):
            logger.error("Groq request timed out")
            return ProviderTimeoutError(
                "The extraction provider timed out. Retry the request.",
                internal_detail="groq timeout",
            )
        if isinstance(exc, groq.BadRequestError):
            return self._handle_bad_request(exc)
        if isinstance(exc, groq.APIError):
            logger.error("Groq API error: %s", exc.__class__.__name__)
            return ProviderError(
                "The extraction provider returned an error.",
                internal_detail=f"groq api error: {exc.__class__.__name__}",
            )
        return ProviderError(
            "The extraction provider returned an error.",
            internal_detail=f"unexpected error: {exc.__class__.__name__}",
        )

    def _handle_bad_request(self, exc: Exception) -> Exception:
        """
        Decide whether a 400 means "use JSON-object mode" or a real failure.

        A 400 mentioning json_schema / response_format indicates the model
        does not support schema mode, which we handle by falling back.
        """
        message = str(getattr(exc, "message", "") or "").lower()
        mentions_schema = "json_schema" in message or "response_format" in message
        if mentions_schema:
            return _FallbackToJsonObject()
        logger.error("Groq rejected the request")
        return ProviderError(
            "The extraction provider rejected the request.",
            internal_detail="groq bad request",
        )


class _FallbackToJsonObject(Exception):
    """Internal signal: retry once using plain JSON-object mode."""
