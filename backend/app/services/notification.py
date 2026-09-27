"""
CareLoop AI — Notification Service (Phase 5)

Owns the delivery lifecycle and the retry policy.

Three responsibilities, kept together because they share one state machine:

  1. Materialisation.  Turning a due reminder occurrence into a `Notification`
     row, exactly once, via a deterministic idempotency key.
  2. Delivery.  Handing the body to a provider and recording the outcome.
  3. Retry.  Deciding whether a failure is worth another attempt and when.

The provider decides transient-vs-permanent; this service decides what to do
about it, so the policy is stated once rather than in every transport.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    NotificationNotFoundError,
    NotificationPermanentError,
    NotificationProviderError,
    NotificationTransientError,
)
from app.core.redaction import redact_secrets
from app.core.timezones import ensure_aware, to_local, utcnow
from app.models.appointment import Appointment
from app.models.medication import Medication
from app.models.notification import (
    DeliveryChannel,
    Notification,
    NotificationStatus,
    NotificationType,
)
from app.models.patient import Patient
from app.models.reminder import Reminder, ReminderStatus, ReminderType
from app.notifications.base import NotificationProvider
from app.notifications.factory import build_provider
from app.repositories.notification import NotificationRepository

logger = logging.getLogger(__name__)

# How long a `sending` row may sit untouched before it is presumed orphaned by
# a killed worker.  Comfortably longer than any provider timeout, so a slow
# but live delivery is never stolen.
STALE_SENDING_AFTER_SECONDS = 900


class NotificationService:
    """Materialises, delivers, and retries notification occurrences."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Optional[Settings] = None,
        provider: Optional[NotificationProvider] = None,
    ) -> None:
        self._db = db
        self._repo = NotificationRepository(db)
        self._settings = settings or get_settings()
        # Provider injected in tests so delivery is exercised without a network.
        self._provider = provider or build_provider(self._settings)

    # ── Idempotency ─────────────────────────────────────────────────────────
    @staticmethod
    def build_idempotency_key(
        reminder_id: uuid.UUID,
        occurrence_at: datetime,
        channel: DeliveryChannel,
    ) -> str:
        """
        The key that makes double-sending impossible.

        Derived only from the three facts that define the delivery: which rule,
        which occurrence, which transport.  It deliberately does NOT include the
        attempt number, so a retry computes the SAME key as the first attempt -
        which is precisely what stops a retry from creating a second message.

        `occurrence_at` is normalised to UTC before formatting so that the same
        occurrence always yields the same string, whatever offset the caller
        happened to pass in.
        """
        normalised = ensure_aware(occurrence_at, field="occurrence_at")
        return f"{reminder_id}:{normalised.isoformat()}:{channel.value}"

    def materialize(
        self,
        reminder: Reminder,
        *,
        occurrence_at: datetime,
    ) -> tuple[Notification, bool]:
        """
        Create the notification row for one occurrence, if it is new.

        Returns:
            `(notification, created)`.  `created=False` means the occurrence was
            already handled; the caller must not deliver it.
        """
        channel = self._channel()
        key = self.build_idempotency_key(
            reminder.id, occurrence_at, channel
        )

        candidate = Notification(
            patient_id=reminder.patient_id,
            reminder_id=reminder.id,
            notification_type=self._notification_type(reminder),
            medication_id=reminder.medication_id,
            appointment_id=reminder.appointment_id,
            scheduled_for=occurrence_at,
            timezone=reminder.timezone,
            status=NotificationStatus.pending,
            channel=channel,
            attempt_count=0,
            body=self._render_body(reminder),
            recipient=self._resolve_recipient(reminder.patient_id),
            idempotency_key=key,
        )
        return self._repo.materialize_if_absent(candidate)

    # ── Delivery ────────────────────────────────────────────────────────────
    def deliver(self, notification_id: uuid.UUID) -> Notification:
        """
        Attempt delivery of one notification and record the outcome.

        Safe to call twice for the same row: the second call finds it terminal
        or already claimed and returns it without sending.
        """
        now = utcnow()
        claimed = self._repo.claim_for_delivery(notification_id, now=now)
        if claimed is None:
            existing = self._repo.get_by_id(notification_id)
            if existing is None:
                raise NotificationNotFoundError()
            # Already sent, failed, or in flight elsewhere.
            return existing

        self._db.commit()

        try:
            receipt = self._provider.send(
                recipient=claimed.recipient, body=claimed.body
            )
        except (NotificationTransientError, NotificationPermanentError) as exc:
            return self._record_failure(claimed, exc)
        except NotificationProviderError as exc:
            # A provider bug that surfaced as a generic provider error. Treated
            # as permanent so a broken adapter cannot loop forever.
            return self._record_failure(claimed, exc)

        return self._record_success(claimed, receipt.provider_message_id)

    def _record_success(
        self,
        notification: Notification,
        provider_message_id: Optional[str],
    ) -> Notification:
        updated = self._repo.update(
            notification,
            status=NotificationStatus.sent,
            sent_at=utcnow(),
            provider_message_id=provider_message_id,
            next_retry_at=None,
            last_error=None,
        )
        self._db.commit()
        logger.info(
            "notification_sent notification_id=%s attempt=%d channel=%s",
            notification.id,
            notification.attempt_count,
            notification.channel.value,
        )
        return updated

    def _record_failure(
        self,
        notification: Notification,
        error: Exception,
    ) -> Notification:
        """
        Record a failed attempt and decide whether to retry.

        The decision table:
          * retries remaining AND the error is retryable -> `failed` with a
            `next_retry_at`, which is what `find_retryable` looks for.
          * budget exhausted, or a permanent error -> `failed` with
            `next_retry_at = NULL`, so it is never picked up again.

        `last_error` is redacted before it is stored: a provider detail can
        echo a token.
        """
        retryable = bool(getattr(error, "retryable", False))
        attempts_made = notification.attempt_count
        max_attempts = self._settings.notification_max_attempts
        should_retry = retryable and attempts_made < max_attempts

        if should_retry:
            delay = self._backoff_seconds(attempts_made)
            next_retry_at = utcnow() + timedelta(seconds=delay)
            logger.warning(
                "notification_retry_scheduled notification_id=%s attempt=%d "
                "of %d delay_seconds=%d reason=%s",
                notification.id,
                attempts_made,
                max_attempts,
                delay,
                type(error).__name__,
            )
        else:
            next_retry_at = None
            logger.error(
                "notification_failed notification_id=%s attempt=%d "
                "retryable=%s reason=%s",
                notification.id,
                attempts_made,
                retryable,
                type(error).__name__,
            )

        updated = self._repo.update(
            notification,
            status=NotificationStatus.failed,
            # Redacted: a vendor error body can echo the access token.
            last_error=self._safe_error(error),
            next_retry_at=next_retry_at,
        )
        self._db.commit()
        return updated

    def _backoff_seconds(self, attempt: int) -> int:
        """
        Exponential backoff, clamped to the configured ceiling.

        Attempt 1 waits the base delay, attempt 2 twice that, and so on.  The
        clamp stops a long-offending notification from being scheduled hours
        out and becoming invisible in history.
        """
        base = self._settings.notification_retry_base_seconds
        ceiling = self._settings.notification_retry_max_seconds
        # `attempt` is 1-based, so the first retry uses base * 2**0.
        exponent = max(0, attempt - 1)
        return min(base * (2**exponent), ceiling)

    def _safe_error(self, error: Exception) -> str:
        """A redacted, bounded, client-safe description of a failure."""
        message = f"{type(error).__name__}: {error}"
        return (redact_secrets(message, settings=self._settings) or "")[:1000]

    # ── Retries ─────────────────────────────────────────────────────────────
    def retry_due(self, *, limit: int = 100) -> dict[str, int]:
        """
        Re-attempt notifications whose backoff has elapsed.

        Idempotent end to end: a row already sent is skipped by the claim, so
        running this twice cannot double-send.
        """
        now = utcnow()
        due = self._repo.find_retryable(now=now, limit=limit)
        sent = failed = 0
        for notification in due:
            outcome = self.deliver(notification.id)
            if outcome.status == NotificationStatus.sent:
                sent += 1
            else:
                failed += 1
        return {"examined": len(due), "sent": sent, "failed": failed}

    def reclaim_stale(self, *, limit: int = 100) -> int:
        """
        Return notifications orphaned by a dead worker to `pending`.

        Called by the maintenance beat.  Without it, a worker killed mid-send
        would leave a row in `sending` permanently, and the patient would
        silently stop receiving reminders.
        """
        cutoff = utcnow() - timedelta(seconds=STALE_SENDING_AFTER_SECONDS)
        reclaimed = self._repo.reclaim_stale(stale_before=cutoff, limit=limit)
        if reclaimed:
            self._db.commit()
            logger.warning(
                "notifications_reclaimed count=%d", len(reclaimed)
            )
        return len(reclaimed)

    # ── Reads ───────────────────────────────────────────────────────────────
    def get(self, notification_id: uuid.UUID) -> Notification:
        notification = self._repo.get_by_id(notification_id)
        if notification is None:
            raise NotificationNotFoundError()
        return notification

    def history_for_patient(
        self,
        patient_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
        status: Optional[NotificationStatus] = None,
    ) -> list[Notification]:
        return self._repo.list_for_patient(
            patient_id, skip=skip, limit=limit, status=status
        )

    def history_for_reminder(
        self,
        reminder_id: uuid.UUID,
        *,
        limit: int = 100,
    ) -> list[Notification]:
        return self._repo.list_for_reminder(reminder_id, limit=limit)

    # ── Message rendering ───────────────────────────────────────────────────
    def _render_body(self, reminder: Reminder) -> str:
        """
        Build the message text from stored references.

        Reads the medication or appointment only to produce a human message.
        Note what it does NOT do: it never parses `frequency`, and it never
        invents a time - the times it prints are the ones that were stored on
        the reminder.
        """
        if reminder.reminder_type == ReminderType.medication:
            return self._render_medication_body(reminder)
        return self._render_appointment_body(reminder)

    def _render_medication_body(self, reminder: Reminder) -> str:
        parts = ["Medication reminder"]
        if reminder.medication_id is not None:
            medication = self._db.get(Medication, reminder.medication_id)
            if medication is not None:
                # The name and dosage are the point of the message, but they
                # are clinical text: never logged, only sent.
                parts.append(medication.name)
                if medication.dosage:
                    parts.append(f"({medication.dosage})")
        if reminder.local_time is not None:
            local = to_local(
                reminder.next_occurrence_at or utcnow(), reminder.timezone
            )
            parts.append(f"at {local.strftime('%H:%M')}")
        if reminder.frequency_text:
            parts.append(f"- as prescribed: {reminder.frequency_text}")
        return " ".join(parts)

    def _render_appointment_body(self, reminder: Reminder) -> str:
        parts = ["Appointment reminder"]
        if reminder.appointment_id is not None:
            appointment = self._db.get(Appointment, reminder.appointment_id)
            if appointment is not None:
                if appointment.doctor_name:
                    parts.append(f"with {appointment.doctor_name}")
                if reminder.appointment_at is not None:
                    local = to_local(
                        reminder.appointment_at, reminder.timezone
                    )
                    parts.append(f"on {local.strftime('%Y-%m-%d %H:%M')}")
        if reminder.lead_time_minutes:
            parts.append(
                f"(starts in {reminder.lead_time_minutes} minutes)"
            )
        return " ".join(parts)

    def _resolve_recipient(self, patient_id: uuid.UUID) -> str:
        """
        The destination address, taken from the patient record.

        Never from the request: a caller must not be able to direct a patient's
        clinical reminder at an arbitrary number.  Falls back to the caregiver
        number when there is no patient number, since a reminder nobody can
        receive is not a reminder.
        """
        patient = self._db.get(Patient, patient_id)
        if patient is None:
            raise NotificationPermanentError(
                "The notification has no recipient because the patient record "
                "was not found."
            )
        recipient = patient.contact_number or patient.caregiver_contact
        if not recipient:
            raise NotificationPermanentError(
                "The patient has no contact number, so this notification "
                "cannot be delivered."
            )
        return recipient

    def _channel(self) -> DeliveryChannel:
        try:
            return DeliveryChannel(self._provider.channel)
        except ValueError:
            # A provider whose channel is not a known enum member.  Fail loudly
            # at send time rather than mis-recording history under the wrong
            # transport.
            raise NotificationPermanentError(
                f"Provider channel {self._provider.channel!r} is not a known "
                "delivery channel."
            )

    @staticmethod
    def _notification_type(reminder: Reminder) -> NotificationType:
        if reminder.reminder_type == ReminderType.medication:
            return NotificationType.medication_reminder
        return NotificationType.appointment_reminder
