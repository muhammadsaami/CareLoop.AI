"""
CareLoop AI - RAG Indexing Service (Phase 3)

Turns one Phase 2 discharge document into retrievable vectors.

DESIGN: EXPLICIT, NOT AUTOMATIC
Indexing is a separate, explicit step (`POST .../index`), NOT a side effect of
the Phase 2 upload pipeline.  Keeping it explicit means Phase 2 behaviour is
untouched, a failed extraction never blocks a successful upload, and an
operator can re-index a document after tuning chunking without re-uploading
it.  Nothing about Phase 1 or Phase 2 changes to accommodate this.

IDEMPOTENCY
Chunk IDs are deterministic, so re-indexing overwrites the same IDs.  The
service additionally DELETES the document's existing chunks before writing,
which is what makes a re-index correct when the text has changed: without the
delete, chunks that shrank out of the new text would linger and be returned
as if they were still part of the document.

HEALTHCARE SAFETY BOUNDARY
This service only copies verbatim clinician-authored text into a vector index.
It does not interpret, summarise, diagnose, or rewrite anything, and it never
logs document text.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    DocumentNotIndexableError,
    RetrievalForbiddenError,
)
from app.core.logging import get_logger
from app.models.discharge_document import ProcessingStatus
from app.rag.chunking import DocumentChunker
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.vector_store import ChromaVectorStore

logger = get_logger(__name__)


@dataclass(frozen=True)
class IndexingResult:
    """Outcome of indexing one document.  Counts only - never text."""

    document_id: uuid.UUID
    patient_id: uuid.UUID
    chunks_indexed: int
    pages_covered: int
    chunk_size: int
    chunk_overlap: int
    embedding_provider: str
    embedding_dimensions: int


class RagIndexingService:
    """Indexes one discharge document's extracted text into ChromaDB."""

    def __init__(
        self,
        db: Session,
        *,
        chunker: Optional[DocumentChunker] = None,
        store: Optional[ChromaVectorStore] = None,
        settings: Optional[Settings] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._chunker = chunker or DocumentChunker(self._settings)
        self._store = store or ChromaVectorStore(
            self._settings, embedding_provider=embedding_provider
        )

    def index_document(
        self, patient_id: uuid.UUID, document_id: uuid.UUID
    ) -> IndexingResult:
        """
        Chunk, embed, and store one document.

        Order matters: the ownership check happens BEFORE any vector-store
        call, so a caller can never use this endpoint to probe for another
        patient's document or to write into their collection partition.
        """
        document = self._load_owned_document(patient_id, document_id)

        self._assert_indexable(document.extracted_text, document)

        chunks = self._chunker.chunk_document(
            document_id=document.id,
            patient_id=document.patient_id,
            text=document.extracted_text or "",
            extraction_run_id=self._latest_run_id(document.id),
        )
        if not chunks:
            raise DocumentNotIndexableError(
                internal_detail=(
                    f"no usable chunks: chars={len(document.extracted_text or '')}"
                ),
            )

        # Remove the previous generation first so stale chunks cannot survive.
        self._store.delete_document(patient_id, document.id)
        self._store.upsert_chunks(chunks)

        result = IndexingResult(
            document_id=document.id,
            patient_id=document.patient_id,
            chunks_indexed=len(chunks),
            pages_covered=len({chunk.page for chunk in chunks if chunk.page}),
            chunk_size=self._settings.rag_chunk_size,
            chunk_overlap=self._settings.rag_chunk_overlap,
            embedding_provider=self._store.embeddings.name,
            embedding_dimensions=self._store.embeddings.dimensions,
        )

        logger.info(
            "Document indexed: document=%s chunks=%s pages=%s provider=%s",
            document.id,
            result.chunks_indexed,
            result.pages_covered,
            result.embedding_provider,
        )
        return result

    # ── Internals ───────────────────────────────────────────────────────────

    def _load_owned_document(self, patient_id: uuid.UUID, document_id: uuid.UUID):
        """
        Fetch the document, enforcing ownership.

        A document belonging to a different patient raises the SAME
        404-shaped error as a document that does not exist, so a caller
        cannot distinguish "not yours" from "not there" and use the difference
        to enumerate other patients' document IDs.
        """
        from app.models.discharge_document import DischargeDocument
        from sqlalchemy import select

        document = self._db.scalars(
            select(DischargeDocument).where(DischargeDocument.id == document_id)
        ).first()

        if document is None or document.patient_id != patient_id:
            logger.info(
                "Indexing refused: document not found for patient "
                "document=%s patient=%s",
                document_id,
                patient_id,
            )
            raise RetrievalForbiddenError

        return document

    @staticmethod
    def _assert_indexable(
        text: Optional[str], document
    ) -> None:
        """
        Refuse to index a document that has no trustworthy source text.

        Only a COMPLETED document with real extracted text may be indexed.
        Indexing a FAILED or PENDING document would store empty or partial
        text and then return it as if it were the clinician's words.
        """
        if document.processing_status is not ProcessingStatus.COMPLETED:
            raise DocumentNotIndexableError(
                internal_detail=f"processing_status={document.processing_status}",
            )
        if not text or not text.strip():
            raise DocumentNotIndexableError(
                internal_detail="extracted_text empty",
            )

    def _latest_run_id(self, document_id: uuid.UUID) -> Optional[uuid.UUID]:
        """
        Attach the newest extraction run ID to each chunk for traceability.

        Optional by design: a document can have text without a completed run
        (for example if extraction failed after text extraction succeeded),
        and that is still legitimate source text to index.
        """
        from sqlalchemy import select

        from app.models.extraction_run import ExtractionRun

        run = self._db.scalars(
            select(ExtractionRun)
            .where(ExtractionRun.document_id == document_id)
            .order_by(ExtractionRun.started_at.desc())
            .limit(1)
        ).first()
        return run.id if run else None
