"""
CareLoop AI — Escalation ORM Model (Phase 6)

One row per (check-in, matched rule). That pair is UNIQUE, which is the whole
idempotency story: re-evaluating a check-in, retrying a Celery task, or two
workers racing cannot produce two escalation events for the same trigger.

WHAT THIS TABLE IS NOT
It is not a diagnosis, not a triage score, and not a clinical severity
assessment. `severity` is a *copy* of the severity label that the document
extraction step already attached to the warning symptom that matched - the
system never derives a new one. `reason_code` is the code of the configured
rule that fired, so an auditor can reconstruct exactly why this row exists
without reading any patient text.

The escalation says: "a configured rule matched, and here is the configured
workflow to follow". What that workflow means clinically is decided by the
care team that configured it, not by this row.
"""
from __future__ import annotations

import enum
import uuid
import datetime as dt

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class EscalationStatus(str, enum.Enum):
    """
    Escalation lifecycle.

    `pending`   - a rule matched; nobody has been told yet.
    `notified`  - a caregiver notification was requested and materialised.
    `acknowledged` - a human has seen it and taken it on.
    `resolved`  - closed out by a human.
    `cancelled` - closed out without action, with a reason.

    The last three are terminal. `notified` is NOT terminal: a caregiver who was
    messaged but never responded has still not acknowledged anything, and
    treating "we sent it" as "it was handled" is the failure mode this enum
    exists to prevent.

    Transitions are enforced in `app.services.escalation.EscalationService`,
    not here, so that the legal moves are stated in one place with the
    permission model alongside them.
    """

    pending = "pending"
    notified = "notified"
    acknowledged = "acknowledged"
    resolved = "resolved"
    cancelled = "cancelled"


#: Statuses from which no further transition is allowed.
TERMINAL_ESCALATION_STATUSES = frozenset(
    {EscalationStatus.resolved, EscalationStatus.cancelled}
)


class EscalationCategory(str, enum.Enum):
    """
    The kind of configured rule that matched.

    Operational, not clinical: these name a *rule family*, not a medical
    condition or a level of urgency the system invented.
    """

    #: A documented warning symptom was reported as present and changed.
    warning_criteria_changed = "warning_criteria_changed"
    #: A documented warning symptom at or above the configured severity floor
    #: was reported as present, whatever its direction of change.
    warning_criteria_present = "warning_criteria_present"


class EscalationWorkflow(str, enum.Enum):
    """
    The CONFIGURED action a human is expected to take.

    This is a deployment decision exposed as data, not a recommendation made by
    the system. Nothing here tells a patient what to do about a condition, and
    nothing here is derived from an LLM.
    """

    #: A clinician or the configured care team reviews the check-in.
    review_by_care_team = "review_by_care_team"
    #: The configured discharge instructions direct the patient to contact
    #: their care team; this escalation routes it to them.
    contact_care_team = "contact_care_team"


class Escalation(Base):
    """
    An auditable record that a configured rule matched a check-in, plus the
    lifecycle of the human workflow it started.
    """

    __tablename__ = "escalations"
    __table_args__ = (
        # THE idempotency guarantee.  A duplicate evaluation of the same
        # check-in under the same rule is rejected by the database.
        Index(
            "uq_escalations_checkin_rule",
            "checkin_id",
            "rule_code",
            unique=True,
        ),
        # "What is outstanding for this patient?" and "what has this patient
        # escalated about?" are the only two reads the API needs.
        Index("ix_escalations_patient_id", "patient_id"),
        Index("ix_escalations_status", "status"),
        Index("ix_escalations_patient_id_created_at", "patient_id", "created_at"),
        Index("ix_escalations_checkin_id", "checkin_id"),
        # The caregiver-notification linkage, indexed because the delivery
        # worker and every escalation detail view join through it.
        Index("ix_escalations_notification_id", "notification_id"),
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
    checkin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("checkins.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Which configured rule fired ─────────────────────────────────────────
    # The stable code, plus the version of the rule set that evaluated it.  An
    # audit answer to "why did this fire?" is therefore reproducible: look up
    # (rule_code, rule_version) in the rule registry and the logic is fixed.
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[EscalationCategory] = mapped_column(
        Enum(EscalationCategory, name="escalation_category"),
        nullable=False,
    )
    workflow: Mapped[EscalationWorkflow] = mapped_column(
        Enum(EscalationWorkflow, name="escalation_workflow"),
        nullable=False,
    )

    # A COPY of the matched warning symptom's stored severity label - never a
    # severity this system derived.  Nullable because a rule can fire on the
    # patient's overall wellbeing rather than on a specific symptom row.
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # The warning symptom that matched, as a reference.  ON DELETE SET NULL: the
    # escalation is a clinical record and must survive the symptom row being
    # tidied up; losing the pointer is preferable to losing the audit trail.
    warning_symptom_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warning_symptoms.id", ondelete="SET NULL"),
        nullable=True,
    )

    # A short, safe, non-diagnostic explanation: the configured rule code and
    # its version.  Deliberately NOT a symptom description and NOT a rendering
    # of the patient's answers, because this column is read by operators.
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)

    # ── Lifecycle ───────────────────────────────────────────────────────────
    status: Mapped[EscalationStatus] = mapped_column(
        Enum(EscalationStatus, name="escalation_status"),
        nullable=False,
        default=EscalationStatus.pending,
    )
    notified_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why a human closed it.  Operational prose only ("reviewed, no action"),
    # never a clinical narrative.
    resolution_note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The caregiver notification this escalation asked for, as a reference.
    # ON DELETE SET NULL for the same audit reason as above.
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Set when a caregiver notification could not be requested - typically no
    # caregiver contact on the patient record.  A code, not an explanation.
    notification_blocked_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
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

    # ── Relationships ───────────────────────────────────────────────────────
    checkin: Mapped["CheckIn"] = relationship(  # noqa: F821
        back_populates="escalations",
        lazy="select",
    )
    # Deliberately NOT `back_populates`-paired with `Notification.escalation`.
    #
    # These two tables carry a FK in each direction - `escalations.notification_id`
    # and `notifications.escalation_id` - so pairing the relationships would
    # describe a single many-to-one link from both ends, which SQLAlchemy
    # rejects ("both of the same direction"). Each side is therefore declared
    # unidirectional against its own explicit foreign key, and the two columns
    # are kept consistent by the service that writes them together
    # (`EscalationService.request_caregiver_notification`) and asserted by a
    # test.  The redundancy is intentional: both directions are genuinely read
    # (the escalation audit trail, and the notification history view), and a
    # join that resolves the clinical record from either side is worth one
    # nullable column.
    notification: Mapped["Notification | None"] = relationship(  # noqa: F821
        lazy="select",
        foreign_keys=[notification_id],
    )

    @property
    def is_terminal(self) -> bool:
        """True once no further lifecycle transition is permitted."""
        return self.status in TERMINAL_ESCALATION_STATUSES

    def __repr__(self) -> str:
        # PHI-conscious: rule code and status only, no symptom description.
        return (
            f"<Escalation id={self.id} rule={self.rule_code} "
            f"status={self.status}>"
        )
