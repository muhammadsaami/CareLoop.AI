"""
CareLoop AI — CheckIn ORM Model

IMPORTANT — Healthcare Safety Boundary:
`flagged=True` is a stored state only.  The application does NOT
automatically determine whether a response indicates a medical emergency.
AI classification of check-in responses belongs to a later phase.
"""
from __future__ import annotations

import uuid
import datetime as dt

from sqlalchemy import Boolean, DateTime, Date, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CheckIn(Base):
    """
    A daily patient check-in response.
    """

    __tablename__ = "checkins"
    __table_args__ = (
        Index("ix_checkins_patient_id", "patient_id"),
        Index("ix_checkins_date", "date"),
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
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)

    # Privacy note: response_text may contain patient health information.
    # Do NOT log this field.
    response_text: Mapped[str] = mapped_column(Text, nullable=False)

    flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    patient: Mapped["Patient"] = relationship(back_populates="checkins")  # noqa: F821

    def __repr__(self) -> str:
        return f"<CheckIn id={self.id} date={self.date} flagged={self.flagged}>"
