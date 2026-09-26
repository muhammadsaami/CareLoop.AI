"""
CareLoop AI - Embedding providers for RAG (Phase 3)

Exports the abstraction, both shipped implementations, and the factory.
Callers should use `get_embedding_provider()`.

  * `SemanticEmbeddingProvider` - real sentence embeddings, run locally via
    ONNX Runtime.  Matches meaning, not wording.
  * `HashingEmbeddingProvider`   - lexical bag-of-words fallback needing no
    model files at all, for air-gapped installs and deterministic tests.
"""
from app.rag.embeddings.base import STOPWORDS, EmbeddingProvider, tokenize
from app.rag.embeddings.factory import (
    PROVIDERS,
    SUPPORTED_PROVIDERS,
    get_embedding_provider,
)
from app.rag.embeddings.hashing import HashingEmbeddingProvider
from app.rag.embeddings.semantic import SemanticEmbeddingProvider

__all__ = [
    "EmbeddingProvider",
    "SemanticEmbeddingProvider",
    "HashingEmbeddingProvider",
    "get_embedding_provider",
    "tokenize",
    "STOPWORDS",
    "PROVIDERS",
    "SUPPORTED_PROVIDERS",
]
