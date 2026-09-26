"""
CareLoop AI - RAG Retrieval Service (Phase 3)

Returns verbatim chunks from ONE discharge document that best match a query.

THIS SERVICE DOES NOT GENERATE AN ANSWER
There is no LLM call anywhere in this module.  It returns the clinician's own
sentences plus the page each came from, and stops there.  Phrase completion,
summarisation, and "what should the patient do" questions are all Phase 4+
work and are explicitly out of scope; answering them from these chunks would
mean generating clinical guidance, which this system must not do.

TENANCY - DEFENCE IN DEPTH
Two independent checks, either of which alone would block a cross-patient
read:
  1. `_load_owned_document` verifies in PostgreSQL that the document belongs
     to the requested patient.  A mismatch produces the same 404-shaped
     error as a missing document, so document IDs cannot be enumerated.
  2. The Chroma query always carries a `where` filter on BOTH `patient_id`
     and `discharge_document_id` (see `vector_store.tenancy_filter`).  The
     filter is a required argument of the store, not an option, so there is
     no code path that reads the collection unfiltered.

HEALTHCARE SAFETY BOUNDARY
Chunks are returned exactly as extracted.  A low `score` means the text was
not a good match and the caller should say so rather than present it as
relevant; the score is returned precisely so that decision stays with the
caller.  Anything below the active embedding provider's relevance floor is
dropped entirely, so a query the document does not address produces an empty
list rather than its least unrelated passage.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import EmptyQueryError, RetrievalForbiddenError
from app.core.logging import get_logger
from app.models.discharge_document import DischargeDocument
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.vector_store import ChromaVectorStore

logger = get_logger(__name__)

# Hard ceiling on how much text one response may return, independent of top_k.
# A caller cannot ask for the whole document back through a large top_k.
_MAX_CHUNK_CHARS = 4000


@dataclass(frozen=True)
class RetrievedChunk:
    """
    One grounded excerpt.

    `text` is verbatim source text.  `source_page` is None only when the
    source had no page marker at all; it is never inferred or approximated.
    """

    chunk_id: str
    text: str
    source_page: Optional[int]
    score: float
    distance: float
    extraction_run_id: Optional[uuid.UUID] = None


@dataclass(frozen=True)
class RetrievalResult:
    """Grounded retrieval output for one document."""

    document_id: uuid.UUID
    patient_id: uuid.UUID
    query_length: int
    indexed_chunks: int
    min_score: float
    chunks: tuple[RetrievedChunk, ...]

    @property
    def match_count(self) -> int:
        return len(self.chunks)


class RagRetrievalService:
    """Tenant-scoped, grounded retrieval over one discharge document."""

    def __init__(
        self,
        db: Session,
        *,
        store: Optional[ChromaVectorStore] = None,
        settings: Optional[Settings] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._store = store or ChromaVectorStore(
            self._settings, embedding_provider=embedding_provider
        )

    def retrieve(
        self,
        patient_id: uuid.UUID,
        document_id: uuid.UUID,
        query: str,
        top_k: Optional[int] = None,
    ) -> RetrievalResult:
        """
        Return the best-matching chunks from one document.

        Raises EmptyQueryError for a blank query and RetrievalForbiddenError
        when the document is not the requested patient's.  An empty result
        list is a normal, successful outcome: it means this document has
        nothing about the query, which is far better than returning a
        loosely-related passage that reads as relevant.
        """
        self._assert_non_empty_query(query)
        self._load_owned_document(patient_id, document_id)

        effective_k = self._resolve_top_k(top_k)
        hits = self._store.query(
            query_text=query.strip(),
            patient_id=patient_id,
            document_id=document_id,
            n_results=effective_k,
        )

        # Drop chunks that are not actually relevant.  The vector store always
        # returns its N nearest neighbours, so without this floor a query about
        # a drug the document never mentions would come back with the least-
        # unrelated passage attached - which a caller could easily mistake for
        # an answer.  Reporting "nothing found" is the safe and truthful result.
        #
        # The floor comes from the embedding PROVIDER, not from settings alone,
        # because its scale is a property of the model: a dense encoder keeps
        # every similarity inside roughly 0.45-0.75, so the lexical provider's
        # 0.10 would let every off-topic chunk through.
        floor = self._store.embeddings.relevance_floor
        relevant = [hit for hit in hits if float(hit["score"]) >= floor]

        chunks = tuple(
            RetrievedChunk(
                chunk_id=str(hit["chunk_id"]),
                text=self._truncate(hit["text"]),
                source_page=self._as_int(hit["metadata"].get("source_page")),
                score=round(float(hit["score"]), 4),
                distance=round(float(hit["distance"]), 4),
                extraction_run_id=self._as_uuid(
                    hit["metadata"].get("extraction_run_id")
                ),
            )
            for hit in relevant
        )

        result = RetrievalResult(
            document_id=document_id,
            patient_id=patient_id,
            query_length=len(query.strip()),
            indexed_chunks=self._store.count(patient_id, document_id),
            min_score=floor,
            chunks=chunks,
        )

        # Counts, the score floor, and the best score only - never the query
        # or the text.
        logger.info(
            "Retrieval complete: document=%s requested=%s matched=%s "
            "top_score=%s min_score=%s",
            document_id,
            effective_k,
            len(chunks),
            chunks[0].score if chunks else "none",
            floor,
        )
        return result

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _assert_non_empty_query(query: str) -> None:
        if not query or not query.strip():
            raise EmptyQueryError

    def _load_owned_document(
        self, patient_id: uuid.UUID, document_id: uuid.UUID
    ) -> DischargeDocument:
        """
        Verify the document exists AND belongs to this patient.

        Both failures raise the identical error so the endpoint cannot be used
        to discover which document IDs exist.
        """
        document = self._db.scalars(
            select(DischargeDocument).where(DischargeDocument.id == document_id)
        ).first()

        if document is None or document.patient_id != patient_id:
            logger.info(
                "Retrieval refused: document not found for patient "
                "document=%s patient=%s",
                document_id,
                patient_id,
            )
            raise RetrievalForbiddenError

        return document

    def _resolve_top_k(self, top_k: Optional[int]) -> int:
        """
        Clamp `top_k` into a safe range.

        A caller cannot request more than `RAG_MAX_TOP_K` results, which is
        what bounds the response size and the work done per request.
        """
        if top_k is None:
            return int(self._settings.rag_default_top_k)
        try:
            requested = int(top_k)
        except (TypeError, ValueError):
            return int(self._settings.rag_default_top_k)
        if requested < 1:
            return 1
        return min(requested, int(self._settings.rag_max_top_k))

    @staticmethod
    def _truncate(text: str) -> str:
        """
        Cap a single chunk's length defensively.

        A chunk is bounded by RAG_CHUNK_SIZE at write time, so this only ever
        fires if configuration changed between indexing and retrieval.  It
        returns whole lines so a dosage is never cut mid-token silently.
        """
        if len(text) <= _MAX_CHUNK_CHARS:
            return text
        clipped = text[:_MAX_CHUNK_CHARS]
        last_newline = clipped.rfind("\n")
        return clipped[:last_newline] if last_newline > _MAX_CHUNK_CHARS // 2 else clipped

    @staticmethod
    def _as_int(value: object) -> Optional[int]:
        try:
            return int(value) if value is not None else None  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_uuid(value: object) -> Optional[uuid.UUID]:
        if not value:
            return None
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None
