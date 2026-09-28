"""
CareLoop AI — CheckIn ORM Model

The model began as a Phase 1 free-text diary entry and is extended (never
rewritten) by Phase 6 into a structured, auditable daily check-in.

WHAT CHANGED IN PHASE 6, AND WHY
Phase 1 stored one blob of `response_text` and let the *caller* assert
`flagged`. That is unusable for anything clinical: free text cannot be matched
deterministically, and a flag supplied by a client is not an assessment.

Phase 6 therefore adds, and does not remove:
  * `responses` - the structured answers, as coded values rather than prose.
  * `status`    - the outcome of deterministic evaluation.
  * `timezone`  - the zone the check-in was answered in (Phase 5 added the
    patient's zone; the check-in snapshots it so history is unambiguous even if
    the patient's zone later changes).
  * `needs_review` / `review_reason` - the honest answer when the response
    could not be evaluated with confidence.

`response_text` is retained and stays nullable: Phase 1 rows and Phase 1
submissions keep working unchanged, and Phase 6 simply does not use it.

HEALTHCARE SAFETY BOUNDARY
Phase 6 computes `status` by running a fixed, versioned rule set over coded
answers. It does NOT diagnose, does NOT predict disease, does NOT assess
clinical severity, and does NOT consult an LLM. An answer it cannot evaluate
confidently is recorded as `needs_review` - it is never silently rounded down
to "fine" and never silently rounded up to a medical emergency. That is the
whole point of the `needs_review` column.
"""
from __future__ import annotations

import enum
import uuid
import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CheckInStatus(str, enum.Enum):
    """
    Outcome of the deterministic evaluation of one check-in.

    `completed` means every answer was evaluated and no rule matched. It is NOT
    a statement that the patient is well - only that nothing in the configured
    rule set fired. The configured rule set is the configured rule set, not
    this system's opinion about the patient.
    """

    completed = "completed"
    #: A configured rule matched.  At least one `Escalation` row exists.
    escalated = "escalated"
    #: Something in the response could not be evaluated reliably, so a human
    #: must look at it.  Never used to mean "looked and found it fine".
    needs_review = "needs_review"


class WellbeingAnswer(str, enum.Enum):
    """Answer to "how are you feeling today?" - a scale, not a diagnosis."""

    good = "good"
    okay = "okay"
    unwell = "unwell"
    very_unwell = "very_unwell"


class ConditionChange(str, enum.Enum):
    """Answer to "better, about the same, or worse?"."""

    better = "better"
    same = "same"
    worse = "worse"


class SymptomChange(str, enum.Enum):
    """
    How one *documented warning symptom* has changed.

    `absent` is a first-class answer rather than "leave it out": a patient who
    reports a symptom as absent has given a real, useful answer, and
    distinguishing it from silence is what keeps the rule layer honest.
    """

    absent = "absent"
    better = "better"
    same = "same"
    worse = "worse"


class CheckIn(Base):
    """
    A daily patient check-in response.

    The Phase 6 columns are additive; the Phase 1 columns are untouched so
    existing rows and existing API callers keep working.
    """

    __tablename__ = "checkins"
    __table_args__ = (
        Index("ix_checkins_patient_id", "patient_id"),
        Index("ix_checkins_date", "date"),
        # One check-in per patient per day.  A retried submission, a double
        # tap, or two workers racing must not produce two records for the same
        # day - the unique index is what settles it, not application hope.
        Index("uq_checkins_patient_date", "patient_id", "date", unique=True),
        # History pagination.
        Index("ix_checkins_patient_id_date", "patient_id", "date"),
        Index("ix_checkins_status", "status"),
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
    # Do NOT log this field.  Phase 6 leaves it NULL: the structured `responses`
    # below is the Phase 6 record, and free text is not needed to evaluate a
    # coded answer set.
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Phase 6: structured evaluation ──────────────────────────────────────
    # Coded answers only - enum VALUES, never a sentence of patient prose.
    # Shape is documented on `DailyCheckInCreate`; it is stored as JSON so the
    # question set can grow without a migration per question, while the values
    # inside it stay a closed enum.
    responses: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[CheckInStatus] = mapped_column(
        Enum(CheckInStatus, name="checkin_status"),
        nullable=False,
        default=CheckInStatus.completed,
    )

    # The zone the patient answered in, snapshotted from `Patient.timezone` at
    # submission so a later change to the patient's zone cannot rewrite history.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # The Phase 5 daily prompt that produced this check-in, when there was one.
    # ON DELETE SET NULL: a cancelled prompt must not erase the clinical record
    # of the answer it prompted.
    reminder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reminders.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Set when the response could not be evaluated reliably.  A code from
    # `app.services.red_flag_rules.REVIEW_CODES` - never a clinical narrative,
    # because this column is surfaced to operators and read in logs.
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    review_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # When the patient finished answering.  Set on submission: a Phase 6
    # check-in is a single request, so "completed" and "submitted" coincide.
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    patient: Mapped["Patient"] = relationship(back_populates="checkins")  # noqa: F821
    escalations: Mapped[list["Escalation"]] = relationship(  # noqa: F821
        back_populates="checkin",
        lazy="select",
    )

    def __repr__(self) -> str:
        # PHI-conscious: no answers, no review narrative.
        return (
            f"<CheckIn id={self.id} date={self.date} status={self.status} "
            f"needs_review={self.needs_review}>"
        )
