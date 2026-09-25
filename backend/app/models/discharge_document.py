"""
CareLoop AI — DischargeDocument ORM Model (Phase 2)

Stores an uploaded hospital discharge document together with the outcome
of the ingestion pipeline (text extraction, OCR, LLM extraction).

Healthcare safety boundary:
- This model stores document bytes and machine-extracted text only.
- Nothing in this module diagnoses, triages, or recommends treatment.
- `needs_review` items are flagged for human review, never auto-approved.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DocumentType(str, enum.Enum):
    """Coarse classification of the uploaded document."""

    DISCHARGE_SUMMARY = "discharge_summary"
    MEDICATION_LIST = "medication_list"
    LAB_REPORT = "lab_report"
    OTHER = "other"


class ProcessingStatus(str, enum.Enum):
    """Overall ingestion pipeline status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class OcrStatus(str, enum.Enum):
    """Result of the OCR stage."""

    NOT_REQUIRED = "not_required"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExtractionStatus(str, enum.Enum):
    """Result of the structured LLM extraction stage."""

    PENDING = "pending"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class DischargeDocument(Base):
    """An uploaded discharge document and its processing metadata."""

    __tablename__ = "discharge_documents"

    # Store enum VALUES (lowercase) rather than member names, matching the
    # Phase 1 tables which persist 'scheduled' / 'low' style values.
    _ENUM_VALUES = staticmethod(lambda enum_cls: [e.value for e in enum_cls])

    __table_args__ = (
        # Duplicate protection: the same file bytes may only be stored once
        # per patient.  See services/discharge_document.py.
        Index(
            "uq_discharge_documents_patient_hash",
            "patient_id",
            "sha256_hash",
            unique=True,
        ),
        Index("ix_discharge_documents_patient_id", "patient_id"),
        Index("ix_discharge_documents_processing_status", "processing_status"),
        Index("ix_discharge_documents_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ── File metadata ────────────────────────────────────────────────────────
    # `original_filename` is a sanitised DISPLAY name only.  It is never used
    # to build a filesystem path.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Server-generated UUID-based name; the only name used on disk.
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Absolute path of the stored file.
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    document_type: Mapped[DocumentType] = mapped_column(
        Enum(
            DocumentType,
            name="discharge_document_type",
            values_callable=_ENUM_VALUES,
        ),
        nullable=False,
        default=DocumentType.DISCHARGE_SUMMARY,
    )

    # ── Processing state ─────────────────────────────────────────────────────
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(
            ProcessingStatus,
            name="discharge_document_processing_status",
            values_callable=_ENUM_VALUES,
        ),
        nullable=False,
        default=ProcessingStatus.PENDING,
    )
    ocr_status: Mapped[OcrStatus] = mapped_column(
        Enum(
            OcrStatus,
            name="discharge_document_ocr_status",
            values_callable=_ENUM_VALUES,
        ),
        nullable=False,
        default=OcrStatus.NOT_REQUIRED,
    )
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        Enum(
            ExtractionStatus,
            name="discharge_document_extraction_status",
            values_callable=_ENUM_VALUES,
        ),
        nullable=False,
        default=ExtractionStatus.PENDING,
    )

    # ── Extracted content ────────────────────────────────────────────────────
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Plain text obtained from the PDF text layer and/or OCR.
    # May contain protected health information: never log this value.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Client-safe failure summary.  Never contains a stack trace.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────────
    # No ORM cascade: discharge documents are clinical records and must not
    # be silently destroyed.
    patient: Mapped["Patient"] = relationship(back_populates="discharge_documents")  # noqa: F821
    extraction_runs: Mapped[list["ExtractionRun"]] = relationship(  # noqa: F821
        back_populates="document",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<DischargeDocument id={self.id} patient={self.patient_id} "
            f"status={self.processing_status}>"
        )
