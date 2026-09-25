"""
CareLoop AI — LLM Provider Abstraction (Phase 2)

`ExtractionService` depends ONLY on the `LLMProvider` interface defined here.
It never imports Groq or Gemini SDK code, so switching or adding a provider
requires no change to the extraction, validation, or persistence layers.

Contract:
  - `extract_structured()` returns a plain dict parsed from the provider's
    JSON response.  Schema validation happens downstream in the extraction
    service, so providers stay dumb transports.
  - Every expected failure mode is translated into a domain exception from
    `app.core.exceptions`, so callers never see a vendor SDK exception.
  - API keys are read from configuration and are never logged, echoed into
    error messages, or included in return values.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    MalformedProviderOutputError,
    MissingAPIKeyError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

# Some models wrap JSON in a markdown fence despite schema instructions.
_FENCED_JSON = re.compile(
    r"```(?:json)?\s*(?P<body>\{.*?\})\s*```", re.DOTALL
)


def parse_json_payload(raw: str) -> dict[str, Any]:
    """
    Parse a provider response body into a dict.

    Tolerates a markdown code fence around the JSON, which some models emit
    even when JSON mode is requested.  Anything that is not a JSON object
    raises MalformedProviderOutputError.
    """
    if raw is None or not str(raw).strip():
        raise MalformedProviderOutputError(
            "The extraction provider returned an empty response."
        )

    candidate = str(raw).strip()
    fenced = _FENCED_JSON.search(candidate)
    if fenced:
        candidate = fenced.group("body").strip()

    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MalformedProviderOutputError(
            "The extraction provider returned malformed output.",
            internal_detail=f"json decode failed: {exc.__class__.__name__}",
        ) from exc

    if not isinstance(parsed, dict):
        raise MalformedProviderOutputError(
            "The extraction provider returned an unexpected structure."
        )
    return parsed


class LLMProvider(ABC):
    """
    Abstract structured-extraction provider.

    Subclasses implement `_complete()` and translate vendor exceptions.
    """

    #: Stable provider identifier recorded in the extraction audit trail.
    name: str = "unknown"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    # ── Public interface used by ExtractionService ──────────────────────────

    @property
    @abstractmethod
    def model(self) -> str:
        """Resolved model identifier for this provider."""

    @abstractmethod
    def is_configured(self) -> bool:
        """True when the provider has everything it needs to run."""

    @abstractmethod
    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        """
        Return structured extraction output as a dict.

        Raises a Provider* domain exception on any failure.
        """

    # ── Shared helpers ──────────────────────────────────────────────────────

    def ensure_configured(self) -> None:
        """Raise a clear configuration error when the provider cannot run."""
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                f"The '{self.name}' extraction provider is not configured. "
                f"Set LLM_PROVIDER={self.name} and the matching API key."
            )

    @staticmethod
    def _timeout(settings: Settings) -> float:
        return float(settings.llm_timeout_seconds)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{self.__class__.__name__} model={self.model!r}>"
