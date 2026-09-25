"""
CareLoop AI — LLM Provider Factory (Phase 2)

Selects the configured provider.  The extraction service calls this and
receives an `LLMProvider`, so it never imports a vendor SDK.

Only the SELECTED provider's API key is required; the other may stay empty.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderNotConfiguredError
from app.core.logging import get_logger
from app.llm.base import LLMProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.groq_provider import GroqProvider

logger = get_logger(__name__)

PROVIDERS: dict[str, type[LLMProvider]] = {
    GroqProvider.name: GroqProvider,
    GeminiProvider.name: GeminiProvider,
}

SUPPORTED_PROVIDERS: frozenset[str] = frozenset(PROVIDERS)


def get_provider(
    name: Optional[str] = None, settings: Optional[Settings] = None
) -> LLMProvider:
    """
    Return the configured provider instance.

    Raises ProviderNotConfiguredError when LLM_PROVIDER names a provider
    that does not exist, so a typo surfaces as a clear configuration
    problem rather than a crash.
    """
    settings = settings or get_settings()
    provider_name = (name or settings.llm_provider or "").strip().lower()

    if provider_name not in PROVIDERS:
        raise ProviderNotConfiguredError(
            f"Unknown LLM provider '{provider_name}'. "
            f"Supported providers: {', '.join(sorted(PROVIDERS))}.",
            internal_detail=f"llm_provider={provider_name!r}",
        )

    provider = PROVIDERS[provider_name](settings)
    logger.info(
        "LLM provider selected: provider=%s model=%s configured=%s",
        provider.name,
        provider.model,
        provider.is_configured(),
    )
    return provider
