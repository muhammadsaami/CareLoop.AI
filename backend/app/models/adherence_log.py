"""
CareLoop AI — AdherenceLog ORM Model
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AdherenceLog(Base):
    """
    Tracks whether a patient took a scheduled medication dose.
    """

    __tablename__ = "adherence_logs"
    __table_args__ = (
        Index("ix_adherence_logs_patient_id", "patient_id"),
        Index("ix_adherence_logs_medication_id", "medication_id"),
        Index("ix_adherence_logs_scheduled_time", "scheduled_time"),
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
    medication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scheduled_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    taken: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    taken_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    patient: Mapped["Patient"] = relationship(back_populates="adherence_logs")  # noqa: F821
    medication: Mapped["Medication"] = relationship(back_populates="adherence_logs")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<AdherenceLog id={self.id} taken={self.taken} "
            f"scheduled={self.scheduled_time}>"
        )
