"""
CareLoop AI — ExtractionRun ORM Model (Phase 2)

An audit record for ONE attempt to run structured LLM extraction against an
uploaded discharge document.

SECURITY RULES (enforced by review, do not relax):
- NEVER store API keys.
- NEVER store authorization headers or bearer tokens.
- NEVER store the raw prompt or the raw provider response.
- NEVER store the full document text (a character COUNT only).

This makes the table safe to expose through the API for debugging and audit.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
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


class ExtractionRunStatus(str, enum.Enum):
    """Lifecycle of a single extraction attempt."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class ExtractionRun(Base):
    """Audit trail for one structured-extraction attempt."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        Index("ix_extraction_runs_document_id", "document_id"),
        Index("ix_extraction_runs_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("discharge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Which provider/model produced (or failed to produce) the extraction.
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[ExtractionRunStatus] = mapped_column(
        Enum(
            ExtractionRunStatus,
            name="extraction_run_status",
            # Persist enum values (lowercase), matching the Phase 1 tables.
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=ExtractionRunStatus.STARTED,
    )

    # Size metric only — the text itself is never persisted here.
    raw_ocr_character_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # Client-safe error summaries.  No stack traces, no provider payloads.
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationship ─────────────────────────────────────────────────────────
    document: Mapped["DischargeDocument"] = relationship(back_populates="extraction_runs")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<ExtractionRun id={self.id} document={self.document_id} "
            f"provider={self.provider} status={self.status}>"
        )
