"""
CareLoop AI - RAG API Schemas (Phase 3)

Request and response models for grounded document retrieval.

WHAT THESE SCHEMAS DELIBERATELY DO NOT CONTAIN
There is no `answer`, `summary`, `interpretation`, `recommendation`, or
`confidence` field.  Phase 3 returns grounded source excerpts and nothing
else.  Adding an answer-shaped field here is the first step toward generating
clinical guidance, which is out of scope for this phase.

`source_page` is `Optional[int]` and is `None` when the source document had
no page marker.  It is never defaulted to 1 and never estimated.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.retrieval import RetrievedChunk, RetrievalResult


# ── Requests ─────────────────────────────────────────────────────────────────

class RagRetrieveRequest(BaseModel):
    """
    Search one discharge document.

    A document_id is REQUIRED and cannot be defaulted or inferred: retrieval is
    always scoped to a document the caller has already been authorised for, so
    there is no "search everything" mode to accidentally expose.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID = Field(
        ..., description="Patient who owns the document"
    )
    document_id: uuid.UUID = Field(
        ..., description="Discharge document to search within"
    )
    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Natural-language search text, e.g. 'what medication dosage'",
    )
    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description=(
            "Maximum number of chunks to return. Clamped server-side to "
            "RAG_MAX_TOP_K."
        ),
    )

    @field_validator("query")
    @classmethod
    def query_must_have_content(cls, value: str) -> str:
        """Reject whitespace-only queries before they reach the vector store."""
        if not value or not value.strip():
            raise ValueError("query must contain at least one non-space character")
        return value


class RagIndexRequest(BaseModel):
    """Explicitly index one discharge document for retrieval."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID = Field(
        ..., description="Patient who owns the document"
    )


# ── Responses ────────────────────────────────────────────────────────────────

class RetrievedChunkResponse(BaseModel):
    """One verbatim excerpt with its provenance."""

    model_config = ConfigDict(from_attributes=True)

    chunk_id: str = Field(..., description="Stable chunk identifier")
    text: str = Field(
        ...,
        description="Verbatim source text exactly as extracted from the document",
    )
    source_page: Optional[int] = Field(
        default=None,
        description=(
            "Page the text came from. Null when the document had no page "
            "marker; never guessed."
        ),
    )
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Similarity in [0, 1], higher is a closer match. A low score "
            "means the passage is not a good match."
        ),
    )
    extraction_run_id: Optional[uuid.UUID] = Field(
        default=None, description="Extraction run that produced the source text"
    )


class RagRetrieveResponse(BaseModel):
    """
    Grounded retrieval result.

    `chunks` may legitimately be empty: the document simply does not mention
    the query.  That is a successful response, not an error.
    """

    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    document_id: uuid.UUID
    query_length: int = Field(
        ..., description="Length of the query; the query itself is not echoed"
    )
    indexed_chunks: int = Field(
        ...,
        ge=0,
        description="Total chunks stored for this document",
    )
    min_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Similarity floor applied to this response, taken from the active "
            "embedding provider. Chunks scoring below it were excluded, so an "
            "empty list means the document does not address the query."
        ),
    )
    match_count: int = Field(
        ..., ge=0, description="Number of chunks returned in this response"
    )
    chunks: List[RetrievedChunkResponse] = Field(default_factory=list)
    grounded: bool = Field(
        default=True,
        description=(
            "Always true in Phase 3: every returned chunk is verbatim source "
            "text. No generated content is ever included."
        ),
    )
    notice: str = Field(
        default=(
            "Source excerpts only. This is not medical advice, a diagnosis, "
            "or a treatment recommendation."
        ),
        description="Fixed safety notice; not derived from document content",
    )

    @classmethod
    def from_result(cls, result: RetrievalResult) -> "RagRetrieveResponse":
        return cls(
            patient_id=result.patient_id,
            document_id=result.document_id,
            query_length=result.query_length,
            indexed_chunks=result.indexed_chunks,
            min_score=result.min_score,
            match_count=result.match_count,
            chunks=[
                RetrievedChunkResponse.model_validate(chunk)
                for chunk in result.chunks
            ],
        )


class RagIndexResponse(BaseModel):
    """
    Outcome of indexing a document.

    Counts and configuration only - never document text.
    """

    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID
    patient_id: uuid.UUID
    chunks_indexed: int = Field(..., ge=0)
    pages_covered: int = Field(
        ..., ge=0, description="Distinct pages represented in the index"
    )
    chunk_size: int
    chunk_overlap: int
    embedding_provider: str
    embedding_dimensions: int = Field(..., gt=0)
    notice: str = Field(
        default=(
            "Indexing makes verbatim source text searchable. It does not "
            "interpret or summarise the document."
        )
    )


__all__ = [
    "RagRetrieveRequest",
    "RagIndexRequest",
    "RetrievedChunkResponse",
    "RagRetrieveResponse",
    "RagIndexResponse",
    "RetrievedChunk",
    "RetrievalResult",
]
