"""
CareLoop AI — Medication ORM Model
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Medication(Base):
    """
    A medication prescribed to a patient post-discharge.

    Phase 1 note: This model stores data only.  No medical interpretation
    or dosage validation is performed by the application.
    """

    __tablename__ = "medications"
    __table_args__ = (
        Index("ix_medications_patient_id", "patient_id"),
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
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dosage: Mapped[str] = mapped_column(String(100), nullable=False)
    frequency: Mapped[str] = mapped_column(String(100), nullable=False)
    timing: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date | None] = mapped_column(nullable=True)
    end_date: Mapped[date | None] = mapped_column(nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # ── Relationships ─────────────────────────────────────────────────────────
    patient: Mapped["Patient"] = relationship(back_populates="medications")  # noqa: F821
    adherence_logs: Mapped[list["AdherenceLog"]] = relationship(  # noqa: F821
        back_populates="medication",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Medication id={self.id} name={self.name!r}>"
