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
    EscalationNotNotifiableError,
    NotificationNotFoundError,
    NotificationPermanentError,
    NotificationProviderError,
    NotificationTransientError,
)
from app.core.redaction import redact_secrets
from app.core.timezones import ensure_aware, to_local, utcnow
from app.models.appointment import Appointment
from app.models.escalation import Escalation, EscalationStatus
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

    @staticmethod
    def build_escalation_idempotency_key(
        escalation_id: uuid.UUID,
        channel: DeliveryChannel,
    ) -> str:
        """
        The key that makes a caregiver escalation notice send exactly once.

        Derived from the escalation and the transport only.  There is no
        occurrence and no attempt number, for the same reason the reminder key
        omits the attempt: a retry MUST recompute the identical key, or the
        uniqueness constraint stops protecting anything and a retried escalation
        alerts the caregiver twice about one event.

        Prefixed with `escalation:` rather than reusing the bare reminder shape,
        so an escalation key can never collide with a reminder key - they are
        different UUIDs drawn from the same sequence, and a collision would
        silently suppress a real notification.
        """
        return f"escalation:{escalation_id}:{channel.value}"

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

    # ── Phase 6: caregiver escalation notices ────────────────────────────────
    def materialize_escalation(
        self,
        escalation: Escalation,
        *,
        caregiver_contact: str,
        patient_timezone: Optional[str] = None,
    ) -> tuple[Notification, bool]:
        """
        Create the caregiver notification for one escalation, if it is new.

        Reuses the Phase 5 delivery pipeline - same repository, same state
        machine, same retry policy, same provider - rather than opening a
        second delivery path that would need its own retries and its own
        history view.  The only Phase 6 additions are the idempotency key, the
        body, and the recipient rule below.

        Returns `(notification, created)`.  `created=False` means this
        escalation was already notified and must not be delivered again.

        THE RECIPIENT IS EXPLICIT AND NON-NEGOTIABLE
        `caregiver_contact` is a required argument, and there is no fallback to
        the patient's own number.  Phase 5's reminder path may fall back, and
        rightly so: a medication reminder nobody can receive is not a
        reminder.  An escalation notice is different - it is a clinical alert
        about a patient, and sending it to the patient would disclose to them
        that the system has flagged them for a care team to look at, before any
        human has decided whether that is the right thing to have done.  So a
        missing caregiver contact raises `EscalationNotNotifiableError` and the
        caller parks the escalation instead of improvising a recipient.
        """
        recipient = (caregiver_contact or "").strip()
        if not recipient:
            raise EscalationNotNotifiableError()

        channel = self._channel()
        key = self.build_escalation_idempotency_key(escalation.id, channel)

        candidate = Notification(
            patient_id=escalation.patient_id,
            escalation_id=escalation.id,
            notification_type=NotificationType.escalation_notice,
            # `scheduled_for` is the moment the escalation was raised, not now:
            # the notice is about an event, and deriving the key from a
            # re-computed "now" would let a retried run create a second row.
            scheduled_for=ensure_aware(
                escalation.created_at, field="escalation.created_at"
            ),
            timezone=patient_timezone,
            status=NotificationStatus.pending,
            channel=channel,
            attempt_count=0,
            body=self._render_escalation_body(escalation),
            recipient=recipient,
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
        self._mark_escalation_notified(updated)
        self._db.commit()
        logger.info(
            "notification_sent notification_id=%s attempt=%d channel=%s",
            notification.id,
            notification.attempt_count,
            notification.channel.value,
        )
        return updated

    def _mark_escalation_notified(self, notification: Notification) -> None:
        """
        Move a pending escalation to `notified` once its notice actually went.

        The escalation's status column means "the caregiver was reached", and
        the delivery outcome is the only place that fact is ever known -
        `EscalationService` materialises the row, which is an intention, not a
        fact.  So the write belongs here.

        This coupling is one-way on purpose.  `EscalationService` already depends
        on this service; having this service reach back into `EscalationService`
        would be a cycle, and the state machine would then be readable from two
        places at once.  Instead this touches the row directly and reuses
        `ALLOWED_TRANSITIONS`' forward step.

        It matters more than it looks: `acknowledge` only accepts `notified`.
        Without this, a caregiver who HAS been told would be recorded as still
        `pending` forever, could never be acknowledged, and the operator view
        would show an outstanding alert that does not exist.

        Only `pending` moves.  A human who acknowledged or cancelled between
        materialisation and delivery has already decided something more
        important than a status column, and a late-arriving delivery receipt
        must not overwrite it.
        """
        if notification.escalation_id is None:
            return
        escalation = self._db.get(Escalation, notification.escalation_id)
        if escalation is None or escalation.status != EscalationStatus.pending:
            return
        escalation.status = EscalationStatus.notified
        escalation.notified_at = notification.sent_at or utcnow()
        logger.info(
            "escalation_notified escalation_id=%s notification_id=%s",
            escalation.id,
            notification.id,
        )

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
        if reminder.reminder_type == ReminderType.checkin:
            return self._render_checkin_body(reminder)
        return self._render_appointment_body(reminder)

    @staticmethod
    def _render_checkin_body(reminder: Reminder) -> str:
        """
        The daily check-in prompt.

        Deliberately the emptiest message the service sends.  It reads nothing
        from the patient's record except the prompt time, which is already on
        the reminder, and it names no condition, no symptom, and no severity.

        That restraint is the point rather than an oversight. The purpose of
        this message is to ask a question, and a patient who is sent "your
        breathlessness is being monitored" learns something alarming from a
        notification that is supposed to be routine - and learns it before any
        rule has evaluated an answer, or anything has actually changed.  The
        prompt is therefore constant, and everything conditional happens after
        the patient answers.
        """
        parts = ["Daily check-in: how are you feeling today?"]
        if reminder.local_time is not None:
            parts.append(
                f"Your daily check-in is set for "
                f"{reminder.local_time.strftime('%H:%M')}."
            )
        return " ".join(parts)

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

    def _render_escalation_body(self, escalation: Escalation) -> str:
        """
        Build the caregiver alert from the escalation's audit fields only.

        WHAT IS INCLUDED, and why each item is safe: the check-in date, the
        rule code and version that fired, the workflow the deployment
        configured, and the severity label copied from the stored
        `WarningSymptom` row.  All four are already in the database before
        this method runs, and all four are what makes the alert actionable
        without a human having to guess why it fired.

        WHAT IS EXCLUDED, and why each exclusion is load-bearing:

          * The patient's answers.  This system holds coded values, but a row
            of them is still the patient's account of their own body, and the
            caregiver's job is to review the record - not to be handed a
            summary that reads like an assessment.
          * The `WarningSymptom` description.  That is text extracted from a
            discharge document, so quoting it risks putting a document excerpt
            in front of someone who is not the care team.  The caregiver can
            open the record; the alert only says which rule fired.
          * Any severity this system derived.  Nothing here ranks or combines
            symptoms.  A label appears only because a human wrote it on the
            warning-symptom row.
          * Any clinical instruction.  No "seek emergency care", no advice,
            no condition name.  The workflow is reported as a category, not as
            a recommendation, and the closing line says plainly that the
            message is not an assessment - so a caregiver who receives this
            cannot mistake it for a clinical opinion from the system.

        The body is stored on the notification row and sent, never logged: the
        delivery log lines in this service record ids and attempt counts only.
        """
        parts = [
            "CareLoop alert: a daily check-in matched a configured "
            "review rule.",
        ]
        checkin = escalation.checkin
        if checkin is not None:
            parts.append(f"Check-in date: {checkin.date.isoformat()}.")
        parts.append(
            f"Rule: {escalation.rule_code} (rule set "
            f"{escalation.rule_version})."
        )
        if escalation.severity:
            # The STORED label, not a computed grade.
            parts.append(f"Documented severity: {escalation.severity}.")
        parts.append(f"Configured workflow: {escalation.workflow.value}.")
        parts.append(
            "Open the CareLoop record to review. This is an automated "
            "notification, not a clinical assessment."
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
        if reminder.reminder_type == ReminderType.checkin:
            return NotificationType.checkin_prompt
        return NotificationType.appointment_reminder
