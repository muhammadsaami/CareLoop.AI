"""
CareLoop AI — Reminder ORM Model (Phase 5)

A `Reminder` is a scheduling RULE derived from structured data that already
exists in this system.  It is never derived from free text by guessing.

Two rules, deliberately shaped differently:

  * Medication reminders are RECURRING and expressed as a wall-clock time in
    the patient's own timezone.  They exist because a `Medication` row has a
    `frequency` ("twice daily with meals") that carries NO clock time.  The
    application therefore does not invent one: a clinician or integrator
    supplies the explicit local time(s), and this model stores them verbatim.
    `frequency_text` keeps the original string for display and audit, but
    nothing is ever parsed out of it to decide *when* to fire.

  * Appointment reminders are ONE-SHOT and derive their instant from the
    existing `Appointment.date`.  The caller supplies only a lead time
    (how early to remind); it never supplies the appointment time, which would
    be inventing a clinical fact.

Timestamps are stored as UTC (`DateTime(timezone=True)`) with the originating
IANA zone preserved alongside, so a reminder created for a patient in
Asia/Kolkata fires at the right instant even if the server, the database, or
the worker's host is in another zone entirely.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ReminderType(str, enum.Enum):
    medication = "medication"
    appointment = "appointment"
    # Phase 6: the daily prompt asking a patient to complete their check-in.
    # It is a `Reminder` rather than a parallel scheduling mechanism precisely
    # so the existing due-scan, catch-up, and idempotency machinery applies to
    # it unchanged.  There is no second scheduler in this codebase.
    checkin = "checkin"


class ReminderStatus(str, enum.Enum):
    """
    Reminder lifecycle.

    `active` is the only status the due-scan considers.  A `paused` reminder
    keeps its history but stops producing new occurrences; `cancelled` is
    terminal and is kept rather than deleted so a delivered notification can
    always be traced back to the rule that produced it.
    """

    active = "active"
    paused = "paused"
    completed = "completed"
    cancelled = "cancelled"


class Recurrence(str, enum.Enum):
    """
    How often a reminder repeats.

    `none` is a single occurrence - the only sensible value for an
    appointment, which happens once.
    """

    none = "none"
    daily = "daily"
    weekly = "weekly"


class Reminder(Base):
    """A patient-specific, timezone-aware scheduling rule."""

    __tablename__ = "reminders"
    __table_args__ = (
        # The due-scan's primary access path.
        Index("ix_reminders_status_next_occurrence", "status", "next_occurrence_at"),
        Index("ix_reminders_patient_id", "patient_id"),
        Index("ix_reminders_medication_id", "medication_id"),
        Index("ix_reminders_appointment_id", "appointment_id"),
        # One active medication reminder per (medication, local time).  Two
        # rules for the same dose at the same time would double-message the
        # patient, so the second is rejected rather than silently merged.
        Index(
            "uq_reminders_medication_slot",
            "medication_id",
            "local_time",
            unique=True,
        ),
        # Phase 6: at most ONE daily check-in prompt per patient per local time.
        # `medication_id` is NULL for check-in reminders, and PostgreSQL treats
        # NULLs as distinct in a unique index, so the slot index above does not
        # cover them - a partial index is required, otherwise a patient could
        # accumulate duplicate daily prompts that all fire on the same day.
        Index(
            "uq_reminders_checkin_slot",
            "patient_id",
            "local_time",
            unique=True,
            postgresql_where=text("reminder_type = 'checkin'"),
        ),
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

    reminder_type: Mapped[ReminderType] = mapped_column(
        Enum(ReminderType, name="reminder_type"),
        nullable=False,
    )
    # Source references.  ON DELETE RESTRICT: a reminder derived from a
    # medication or appointment must not outlive it silently.
    medication_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medications.id", ondelete="RESTRICT"),
        nullable=True,
    )
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # ── Medication rule (recurring) ────────────────────────────────────────
    # The exact clock time the patient takes this dose, in THEIR timezone.
    # Supplied explicitly by a clinician/integrator; never inferred from the
    # medication's free-text frequency.
    local_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    recurrence: Mapped[Recurrence] = mapped_column(
        Enum(Recurrence, name="reminder_recurrence"),
        nullable=False,
        default=Recurrence.daily,
    )
    recurrence_interval: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    # The medication's own `frequency` string, copied at creation time for
    # display and audit.  Display only - see the module docstring.
    frequency_text: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Appointment rule (one-shot) ────────────────────────────────────────
    # The appointment's own instant (UTC), and how many minutes BEFORE it the
    # reminder fires.  The caller supplies only the lead time.
    appointment_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lead_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Scheduling ─────────────────────────────────────────────────────────
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    # The next instant this rule is due, in UTC.  Maintained by the scheduler
    # so the due-scan is a single indexed range query rather than a full scan
    # with per-row timezone maths.
    next_occurrence_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional window in which a recurring reminder is still useful.  A null
    # end date means "until cancelled".
    active_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus, name="reminder_status"),
        nullable=False,
        default=ReminderStatus.active,
    )
    # Set when the schedule disagrees with the source data it was derived
    # from - for example three daily dose times against a frequency reading
    # "once daily".  The reminder is still created; a human decides.
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    review_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # ── Relationships ──────────────────────────────────────────────────────
    notifications: Mapped[list["Notification"]] = relationship(  # noqa: F821
        back_populates="reminder",
        lazy="select",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        # PHI-conscious: no medication name, no free-text frequency.
        return (
            f"<Reminder id={self.id} type={self.reminder_type} "
            f"status={self.status} tz={self.timezone}>"
        )
