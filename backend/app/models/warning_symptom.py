"""
CareLoop AI — WarningSymptom ORM Model

IMPORTANT — Healthcare Safety Boundary:
This model stores documented warning symptoms as structured data only.
The application does NOT infer medical danger, does NOT diagnose,
and does NOT recommend emergency actions in Phase 1.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SymptomSeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class WarningSymptom(Base):
    """
    A documented warning symptom associated with a patient.
    severity is a structured label — it does not constitute a medical assessment.
    """

    __tablename__ = "warning_symptoms"
    __table_args__ = (
        Index("ix_warning_symptoms_patient_id", "patient_id"),
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
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[SymptomSeverity] = mapped_column(
        Enum(SymptomSeverity, name="symptom_severity"),
        nullable=False,
    )

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
    patient: Mapped["Patient"] = relationship(back_populates="warning_symptoms")  # noqa: F821

    def __repr__(self) -> str:
        return f"<WarningSymptom id={self.id} severity={self.severity}>"
