"""
CareLoop AI — Patient ORM Model
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Patient(Base):
    """
    Core patient record.

    Privacy note: contact_number is PII — do not include it in log
    messages or API responses beyond what is strictly necessary.
    """

    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # NULL for a patient created by self-registration, which does not collect a
    # phone number.  The notification layer treats a missing contact as "not
    # deliverable" rather than silently dropping the reminder.
    contact_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    caregiver_contact: Mapped[str | None] = mapped_column(String(50), nullable=True)
    discharge_date: Mapped[date | None] = mapped_column(nullable=True)

    # Phase 5: the IANA timezone this patient actually lives in, e.g.
    # "Asia/Kolkata".  Every reminder created for this patient is interpreted
    # in this zone, so "08:00 daily" means 08:00 where the patient is, not
    # where the server happens to run.
    #
    # Defaults to UTC rather than being nullable: an unknown timezone must not
    # silently shift a dose by hours.  UTC is the conservative choice (it is
    # what the whole system already assumed before Phase 5) and it is
    # corrected explicitly, with a 422, if it is ever wrong.
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="UTC",
        server_default="UTC",
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
    # cascade="all, delete-orphan" is intentionally NOT used here.
    # Healthcare records should not be silently destroyed.
    # Deletion of a patient with existing records should be prevented at
    # the application level (service layer).
    medications: Mapped[list["Medication"]] = relationship(  # noqa: F821
        back_populates="patient",
        lazy="select",
    )
    appointments: Mapped[list["Appointment"]] = relationship(  # noqa: F821
        back_populates="patient",
        lazy="select",
    )
    warning_symptoms: Mapped[list["WarningSymptom"]] = relationship(  # noqa: F821
        back_populates="patient",
        lazy="select",
    )
    checkins: Mapped[list["CheckIn"]] = relationship(  # noqa: F821
        back_populates="patient",
        lazy="select",
    )
    adherence_logs: Mapped[list["AdherenceLog"]] = relationship(  # noqa: F821
        back_populates="patient",
        lazy="select",
    )
    # Phase 2: discharge documents are clinical records and are likewise
    # never cascade-deleted.
    discharge_documents: Mapped[list["DischargeDocument"]] = relationship(  # noqa: F821
        back_populates="patient",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Patient id={self.id} name={self.name!r}>"
