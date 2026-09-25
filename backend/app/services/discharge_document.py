"""
CareLoop AI — DischargeDocument Service (Phase 2)

Top-level orchestration for the ingestion pipeline:

    validate -> store -> duplicate check -> PDF/image processing ->
    OCR -> structured LLM extraction -> validation -> persistence

Failure policy:
  - A document that is stored but cannot be fully processed is RETAINED
    with `processing_status=failed` and a client-safe `error_message`, so a
    human can inspect it and reprocess.  Losing the document on an OCR or
    provider outage would be worse than keeping a failed record.
  - A document rejected BEFORE storage (bad type, oversized, duplicate) is
    never written to disk.
  - No stack trace ever reaches the client.
"""
from __future__ import annotations

import time
import uuid
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CareLoopError,
    DocumentNotFoundError,
    DuplicateDocumentError,
    FileTooLargeError,
    StorageError,
)
from app.core.logging import get_logger
from app.models.discharge_document import (
    DischargeDocument,
    DocumentType,
    ExtractionStatus,
    OcrStatus,
    ProcessingStatus,
)
from app.models.extraction_run import ExtractionRunStatus
from app.repositories.discharge_document import DischargeDocumentRepository
from app.schemas.extraction import (
    ExtractionCounts,
    ExtractionResultResponse,
    StructuredDischargeExtraction,
)
from app.llm.base import LLMProvider
from app.services.document_processing import DocumentProcessingService
from app.services.extraction import ExtractionService
from app.services.patient import PatientService
from app.services.storage import DocumentStorageService

logger = get_logger(__name__)


class DischargeDocumentService:
    """Upload, process, and query discharge documents."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Optional[Settings] = None,
        storage: Optional[DocumentStorageService] = None,
        processor: Optional[DocumentProcessingService] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._repo = DischargeDocumentRepository(db)
        self._patients = PatientService(db)
        self._storage = storage or DocumentStorageService(self._settings)
        self._processor = processor or DocumentProcessingService(self._settings)
        # Injectable so tests can substitute a provider without any network
        # access.  Production leaves this None and the configured provider
        # is resolved by the extraction service.
        self._llm_provider = llm_provider

    # ── Upload + full pipeline ──────────────────────────────────────────────

    def upload_and_process(
        self,
        *,
        patient_id: uuid.UUID,
        filename: Optional[str],
        content_type: Optional[str],
        data: bytes,
        run_extraction: bool = True,
    ) -> ExtractionResultResponse:
        """
        Store an uploaded document and run the ingestion pipeline.

        The document row is created only after the file passes validation,
        so a rejected upload leaves no trace on disk or in the database.
        """
        started = time.perf_counter()

        # The patient must already exist.  Patients are never created from
        # a document.
        self._require_patient(patient_id)

        # 1. Validate + write to secure local storage.
        stored = self._storage.store(
            data=data, filename=filename, content_type=content_type
        )

        # 2. Duplicate detection on (patient_id, sha256).
        existing = self._repo.find_by_hash(patient_id, stored.sha256_hash)
        if existing is not None:
            # Remove the redundant copy we just wrote; the original stands.
            self._storage.delete(stored.stored_filename)
            raise DuplicateDocumentError(
                "This exact document has already been uploaded for this "
                "patient. No duplicate was created.",
                internal_detail=f"duplicate of document={existing.id}",
            )

        # 3. Create the document record in PENDING state.
        document = self._repo.create(
            patient_id=patient_id,
            original_filename=stored.original_filename,
            stored_filename=stored.stored_filename,
            file_path=stored.file_path,
            content_type=stored.content_type,
            file_size=stored.file_size,
            sha256_hash=stored.sha256_hash,
            document_type=DocumentType.DISCHARGE_SUMMARY,
            processing_status=ProcessingStatus.PROCESSING,
            ocr_status=OcrStatus.NOT_REQUIRED,
        )
        self._db.commit()
        logger.info(
            "Discharge document stored: document=%s patient=%s size=%s",
            document.id,
            patient_id,
            document.file_size,
        )

        # 4. Process + extract.  Failures are recorded, then re-raised.
        try:
            return self._process_document(
                document=document,
                data=data,
                run_extraction=run_extraction,
                started=started,
            )
        except CareLoopError as exc:
            self._mark_failed(document, exc)
            raise
        except IntegrityError:
            # Lost a race against a concurrent upload of the same bytes.
            self._db.rollback()
            self._storage.delete(document.stored_filename)
            raise DuplicateDocumentError(
                "This exact document has already been uploaded for this "
                "patient."
            ) from None
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            self._mark_failed(document, exc)
            raise StorageError(
                "Failed to process the uploaded document.",
                internal_detail=f"unexpected error: {exc.__class__.__name__}",
            ) from exc

    # ── Pipeline stages ─────────────────────────────────────────────────────

    def _process_document(
        self,
        *,
        document: DischargeDocument,
        data: bytes,
        run_extraction: bool,
        started: float,
    ) -> ExtractionResultResponse:
        # ── Stage: PDF/image processing + OCR ──────────────────────────────
        processed = self._processor.process(
            data=data,
            content_type=document.content_type,
            filename=document.original_filename,
        )

        self._repo.update(
            document,
            extracted_text=processed.text,
            page_count=processed.page_count,
            ocr_status=(
                OcrStatus.COMPLETED if processed.ocr_used else OcrStatus.NOT_REQUIRED
            ),
            processing_status=ProcessingStatus.COMPLETED,
        )
        self._db.commit()

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Document text extracted: document=%s patient=%s pages=%s "
            "ocr=%s chars=%s duration_ms=%s",
            document.id,
            document.patient_id,
            processed.page_count,
            processed.ocr_used,
            processed.char_count,
            duration_ms,
        )

        # ── Stage: structured LLM extraction ───────────────────────────────
        if not run_extraction:
            return self._build_response(
                document=document,
                provider="skipped",
                model="skipped",
                counts=ExtractionCounts(),
                created={},
                message="Document stored and text extracted; "
                "structured extraction was skipped.",
            )

        extraction_service = ExtractionService(
            self._db, settings=self._settings, provider=self._llm_provider
        )
        provider = extraction_service.provider
        try:
            extraction, counts, created = extraction_service.extract_and_persist(
                document=document, document_text=processed.text
            )
        except CareLoopError:
            # Text extraction already succeeded, so this document did reach
            # the extraction stage.  Record that explicitly; otherwise the
            # document would sit at 'pending' forever and a UI could not
            # distinguish "not started" from "failed".
            self._repo.update(document, extraction_status=ExtractionStatus.FAILED)
            self._db.commit()
            raise

        return self._build_response(
            document=document,
            provider=provider.name,
            model=provider.model,
            counts=counts,
            created=created,
            extraction=extraction,
        )

    # ── Queries ─────────────────────────────────────────────────────────────

    def _require_patient(self, patient_id: uuid.UUID) -> None:
        """
        Confirm the patient exists, raising a Phase 2 domain error.

        The Phase 1 services raise FastAPI's HTTPException directly.  This
        Phase 2 service must not leak an HTTP-layer exception out of the
        service boundary, so the 404 is translated into a domain error that
        the global handler renders identically.
        """
        from fastapi import HTTPException

        try:
            self._patients.get_patient(patient_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise DocumentNotFoundError(
                    "No patient exists with this ID. A discharge document "
                    "must be uploaded for an existing patient.",
                    internal_detail=f"patient lookup 404: {patient_id}",
                ) from exc
            raise

    def get_document(self, document_id: uuid.UUID) -> DischargeDocument:
        document = self._repo.get_by_id(document_id)
        if document is None:
            raise DocumentNotFoundError()
        return document

    def list_for_patient(
        self, patient_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> List[DischargeDocument]:
        self._require_patient(patient_id)
        return self._repo.list_for_patient(patient_id, skip=skip, limit=limit)

    # ── Reprocessing ────────────────────────────────────────────────────────

    def reprocess(
        self, document_id: uuid.UUID
    ) -> ExtractionResultResponse:
        """
        Re-run extraction for a document that previously failed.

        Re-reads the stored file and re-runs processing, OCR, and
        structured extraction.  Useful after fixing Tesseract setup or
        adding a provider API key.
        """
        document = self.get_document(document_id)
        started = time.perf_counter()

        if not self._storage.exists(document.stored_filename):
            raise StorageError(
                "The stored document file is missing and cannot be "
                "reprocessed.",
                internal_detail=f"missing file for document={document_id}",
            )
        data = self._storage.read(document.stored_filename)

        self._repo.update(document, processing_status=ProcessingStatus.PROCESSING)
        self._db.commit()

        try:
            return self._process_document(
                document=document,
                data=data,
                run_extraction=True,
                started=started,
            )
        except CareLoopError as exc:
            self._mark_failed(document, exc)
            raise
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            self._mark_failed(document, exc)
            raise StorageError(
                "Failed to reprocess the document.",
                internal_detail=f"unexpected error: {exc.__class__.__name__}",
            ) from exc

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _mark_failed(self, document: DischargeDocument, exc: Exception) -> None:
        """
        Persist a client-safe failure summary on the document.

        The stored file is intentionally RETAINED so the document can be
        reprocessed once the underlying problem is fixed.
        """
        try:
            self._db.rollback()
            message = getattr(exc, "message", None) or (
                "Processing failed unexpectedly."
            )
            self._repo.update(
                document,
                processing_status=ProcessingStatus.FAILED,
                error_message=message[:1000],
            )
            self._db.commit()
            logger.warning(
                "Discharge document processing failed: document=%s "
                "patient=%s error=%s",
                document.id,
                document.patient_id,
                exc.__class__.__name__,
            )
        except Exception:  # noqa: BLE001
            self._db.rollback()
            logger.exception(
                "Could not record failure for document=%s", document.id
            )

    def _build_response(
        self,
        *,
        document: DischargeDocument,
        provider: str,
        model: str,
        counts: ExtractionCounts,
        created: dict[str, list[str]],
        message: Optional[str] = None,
        extraction: Optional[StructuredDischargeExtraction] = None,
    ) -> ExtractionResultResponse:
        return ExtractionResultResponse(
            document_id=str(document.id),
            patient_id=str(document.patient_id),
            provider=provider,
            model=model,
            processing_status=document.processing_status.value,
            ocr_status=document.ocr_status.value,
            extraction_status=document.extraction_status.value,
            page_count=document.page_count,
            counts=counts,
            created_medication_ids=created.get("medications", []),
            created_appointment_ids=created.get("appointments", []),
            created_warning_symptom_ids=created.get("warning_symptoms", []),
            message=message,
        )


# Re-exported so route modules can import the size error from one place.
__all__ = [
    "DischargeDocumentService",
    "FileTooLargeError",
    "ExtractionRunStatus",
]
