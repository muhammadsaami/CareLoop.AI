"""
CareLoop AI - Embedding Provider Factory (Phase 3)

Selects the configured embedding provider.  The RAG services call this and
receive an `EmbeddingProvider`, so they never import a concrete
implementation.

Adding a provider is a two-step change: subclass `EmbeddingProvider` and
register it below.  No chunking, indexing, retrieval, or API code changes.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import Settings, get_settings
from app.core.exceptions import EmbeddingNotConfiguredError
from app.core.logging import get_logger
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.hashing import HashingEmbeddingProvider
from app.rag.embeddings.semantic import SemanticEmbeddingProvider

logger = get_logger(__name__)

PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    SemanticEmbeddingProvider.name: SemanticEmbeddingProvider,
    HashingEmbeddingProvider.name: HashingEmbeddingProvider,
}

SUPPORTED_PROVIDERS: frozenset[str] = frozenset(PROVIDERS)


def get_embedding_provider(
    name: Optional[str] = None, settings: Optional[Settings] = None
) -> EmbeddingProvider:
    """
    Return the configured embedding provider instance.

    Raises EmbeddingNotConfiguredError when the configured name is unknown,
    so a typo surfaces as a clear configuration problem rather than a crash
    deep inside the vector store.
    """
    settings = settings or get_settings()
    provider_name = (name or settings.rag_embedding_provider or "").strip().lower()

    if provider_name not in PROVIDERS:
        raise EmbeddingNotConfiguredError(
            f"Unknown embedding provider '{provider_name}'. "
            f"Supported providers: {', '.join(sorted(PROVIDERS))}.",
            internal_detail=f"rag_embedding_provider={provider_name!r}",
        )

    provider = PROVIDERS[provider_name](settings)
    logger.info(
        "Embedding provider selected: provider=%s dimensions=%s "
        "min_score=%s configured=%s",
        provider.name,
        provider.dimensions if provider.is_configured() else "unknown",
        provider.relevance_floor,
        provider.is_configured(),
    )
    return provider
