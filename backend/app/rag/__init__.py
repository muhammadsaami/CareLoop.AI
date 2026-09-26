"""
CareLoop AI - Retrieval-Augmented Generation (Phase 3)

Grounded retrieval over Phase 2 discharge documents.

Layering, outermost first:
    routes  ->  services (indexing, retrieval)  ->  vector_store  ->  chroma
                     |                                    |
                 chunking                            embeddings

Every layer depends on abstractions defined in this package, so swapping the
vector store or the embedding provider does not ripple upward.

HEALTHCARE SAFETY BOUNDARY
The whole package RETRIEVES verbatim clinician-authored text.  It contains no
answer generation, no summarisation, and no clinical interpretation.  See
`app.rag.retrieval` for the reasoning.
"""
from app.rag.chunking import DocumentChunker, TextChunk
from app.rag.embeddings import EmbeddingProvider, get_embedding_provider
from app.rag.indexing import IndexingResult, RagIndexingService
from app.rag.retrieval import RetrievedChunk, RetrievalResult, RagRetrievalService
from app.rag.vector_store import ChromaVectorStore, tenancy_filter

__all__ = [
    "DocumentChunker",
    "TextChunk",
    "EmbeddingProvider",
    "get_embedding_provider",
    "ChromaVectorStore",
    "tenancy_filter",
    "RagIndexingService",
    "IndexingResult",
    "RagRetrievalService",
    "RetrievalResult",
    "RetrievedChunk",
]
