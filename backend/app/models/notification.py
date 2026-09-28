"""
CareLoop AI — Notification ORM Model (Phase 5)

One row per *delivery attempt sequence* for one reminder occurrence.

The distinction between `Reminder` and `Notification` is deliberate and is what
makes idempotency possible:

  * A `Reminder` is a rule ("Paracetamol at 08:00 every day, Europe/London").
    It is data, not an event.
  * A `Notification` is a materialised occurrence ("the 2026-10-01T08:00
    instance of that rule"). It is created exactly once and then transitions
    through a delivery lifecycle.

Because an occurrence is a row with a UNIQUE `idempotency_key`, a task that
runs twice - a Celery redelivery, a retried dispatch, two workers racing -
cannot produce two messages.  The second attempt finds the existing row and
returns it unchanged.  That guarantee is enforced by the database, not by
application-level hope.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class NotificationType(str, enum.Enum):
    """What the notification is about."""

    medication_reminder = "medication_reminder"
    appointment_reminder = "appointment_reminder"
    # Phase 6: the daily prompt asking the patient to complete a check-in.
    checkin_prompt = "checkin_prompt"
    # Phase 6: a caregiver-facing notice that a configured escalation rule
    # matched.  It deliberately does NOT carry the patient's answers, the
    # matched symptom's text, or any clinical interpretation.
    escalation_notice = "escalation_notice"


class NotificationStatus(str, enum.Enum):
    """
    Delivery lifecycle.

    A notification ends in exactly one terminal state: `sent`, `failed`, or
    `skipped`.  `pending` and `sending` are the two in-flight states, and a
    row is only ever `sending` while a worker holds it - see
    `NotificationService.claim_for_delivery` for how a crashed worker's row is
    reclaimed.
    """

    pending = "pending"   # materialised, not yet attempted
    sending = "sending"   # claimed by a worker
    sent = "sent"         # provider accepted it (terminal, success)
    failed = "failed"     # exhausted retries, or a permanent error (terminal)
    skipped = "skipped"   # deliberately not sent, e.g. the patient went inactive (terminal)


class DeliveryChannel(str, enum.Enum):
    """
    Transport.  Extensible: adding one requires a provider implementation and
    a new enum member, and no change to the scheduling or retry logic.
    """

    console = "console"
    whatsapp = "whatsapp"


class Notification(Base):
    """A single, persistent, auditable notification delivery record."""

    __tablename__ = "notifications"
    __table_args__ = (
        # The due-scan reads by status and scheduled time.
        Index("ix_notifications_status_scheduled_for", "status", "scheduled_for"),
        Index("ix_notifications_patient_id", "patient_id"),
        Index("ix_notifications_reminder_id", "reminder_id"),
        # Duplicate prevention.  A retried task that tries to materialise the
        # same occurrence twice is rejected by the database itself.
        Index(
            "uq_notifications_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
        # History pagination.
        Index("ix_notifications_patient_id_created_at", "patient_id", "created_at"),
        # Phase 6: "which notification did this escalation ask for?" and the
        # reverse "what did we tell the caregiver about this escalation?".
        Index("ix_notifications_escalation_id", "escalation_id"),
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
    reminder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reminders.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Phase 6: set when this notification was materialised to satisfy an
    # escalation.  NULL for Phase 5 medication/appointment/check-in prompts.
    # ON DELETE SET NULL so a deleted notification does not take the clinical
    # audit record with it.
    escalation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("escalations.id", ondelete="SET NULL"),
        nullable=True,
    )

    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type"),
        nullable=False,
    )
    # Source records are kept as references, not copies, so a reminder always
    # resolves to the medication or appointment it was derived from.  ON DELETE
    # RESTRICT: history must not be rewritten because a source row changed.
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

    # ── Scheduling ─────────────────────────────────────────────────────────
    # The instant the notification is DUE, always stored as UTC.  The
    # originating wall-clock time and zone are kept alongside it so history
    # can be rendered in the patient's own time without re-deriving anything.
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── Delivery ───────────────────────────────────────────────────────────
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status"),
        nullable=False,
        default=NotificationStatus.pending,
    )
    channel: Mapped[DeliveryChannel] = mapped_column(
        Enum(DeliveryChannel, name="delivery_channel"),
        nullable=False,
        default=DeliveryChannel.console,
    )
    # The provider's own identifier for the message, when it returns one.
    # Stored for support and de-duplication at the provider's side.
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A redacted, client-safe error CATEGORY plus a short detail.  Never the
    # raw provider payload, which can echo the recipient or the body.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Message ────────────────────────────────────────────────────────────
    # The rendered body.  It is NOT logged by any Phase 5 code path, and the
    # logging tests assert that.  It is stored because notification history is
    # a record of what the patient was actually told.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Who it was addressed to.  Recipients come from the patient record and
    # are never accepted from the request body.
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)

    # ── Idempotency ────────────────────────────────────────────────────────
    # Deterministic function of (reminder, occurrence instant, channel).
    # Recomputed on every attempt, so two workers racing produce the same key
    # and the UNIQUE index resolves the race.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # ── Relationships ──────────────────────────────────────────────────────
    reminder: Mapped["Reminder"] = relationship(  # noqa: F821
        back_populates="notifications",
        lazy="select",
    )
    # Unidirectional against its own FK, for the same reason as
    # `Escalation.notification`: the two tables hold a FK in each direction, so
    # these cannot be `back_populates`-paired. See the note on the Escalation
    # side, which explains why the redundancy is kept.
    escalation: Mapped["Escalation | None"] = relationship(  # noqa: F821
        lazy="select",
        foreign_keys=[escalation_id],
    )

    @property
    def is_terminal(self) -> bool:
        """True once the notification can no longer change state."""
        return self.status in {
            NotificationStatus.sent,
            NotificationStatus.failed,
            NotificationStatus.skipped,
        }

    def __repr__(self) -> str:
        # PHI-conscious: no body, no recipient.
        return (
            f"<Notification id={self.id} type={self.notification_type} "
            f"status={self.status} attempts={self.attempt_count}>"
        )
