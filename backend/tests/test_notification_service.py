"""
CareLoop AI — Notification Delivery Tests (Phase 5)

Covers the delivery lifecycle and the properties that keep a patient from
receiving a duplicate or losing a message:

  * the idempotency key is stable across retries and attempt counts,
  * materialising the same occurrence twice yields ONE row,
  * a transient failure is retried with exponential backoff and then succeeds,
  * a permanent failure is never retried,
  * the retry budget is finite, so a broken endpoint cannot loop forever,
  * a provider error detail is redacted before it is persisted,
  * a `sending` row orphaned by a dead worker is reclaimed.
"""
from datetime import timedelta

import pytest

from app.core.exceptions import (
    NotificationPermanentError,
    NotificationProviderNotConfiguredError,
    NotificationTransientError,
)
from app.core.timezones import utcnow
from app.models.notification import (
    DeliveryChannel,
    NotificationStatus,
    NotificationType,
)
from app.notifications.base import NotificationProvider
from app.services.notification import NotificationService, STALE_SENDING_AFTER_SECONDS
from tests.conftest import RecordingNotificationProvider


def _medication_reminder(phase5_services, make_patient, make_medication, **kwargs):
    """Create one active daily medication reminder at 08:00."""
    from datetime import time

    from app.schemas.reminder import MedicationReminderCreate

    patient = make_patient(**kwargs)
    patient.timezone = "UTC"
    phase5_services["db"].flush()
    medication = make_medication(patient)
    reminders = phase5_services["reminders"].create_medication_reminder(
        patient.id,
        MedicationReminderCreate(medication_id=medication.id, times=[time(8, 0)]),
    )
    return patient, medication, reminders[0]


# ── Idempotency ──────────────────────────────────────────────────────────────


class TestIdempotencyKey:
    def test_key_is_stable_across_calls(self):
        """Two workers computing the key for the same occurrence agree."""
        import uuid

        from app.services.notification import NotificationService as NS

        reminder_id = uuid.uuid4()
        occurrence = utcnow().replace(microsecond=0)
        first = NS.build_idempotency_key(
            reminder_id, occurrence, DeliveryChannel.console
        )
        second = NS.build_idempotency_key(
            reminder_id, occurrence, DeliveryChannel.console
        )
        assert first == second

    def test_key_does_not_depend_on_the_attempt_number(self):
        """
        The reason a retry cannot create a second message.

        If the attempt number were in the key, attempt 2 would be a "different"
        occurrence and would be inserted as a new row.
        """
        import uuid

        from app.services.notification import NotificationService as NS

        reminder_id = uuid.uuid4()
        occurrence = utcnow().replace(microsecond=0)
        attempt_one = NS.build_idempotency_key(
            reminder_id, occurrence, DeliveryChannel.console
        )
        attempt_two = NS.build_idempotency_key(  # same inputs, "later" attempt
            reminder_id, occurrence, DeliveryChannel.console
        )
        assert attempt_one == attempt_two
        assert "attempt" not in attempt_one

    def test_key_differs_by_reminder_occurrence_and_channel(self):
        import uuid

        from app.services.notification import NotificationService as NS

        a, b = uuid.uuid4(), uuid.uuid4()
        t = utcnow().replace(microsecond=0)
        later = t + timedelta(days=1)

        keys = {
            NS.build_idempotency_key(a, t, DeliveryChannel.console),
            NS.build_idempotency_key(b, t, DeliveryChannel.console),
            NS.build_idempotency_key(a, later, DeliveryChannel.console),
            NS.build_idempotency_key(a, t, DeliveryChannel.whatsapp),
        }
        assert len(keys) == 4

    def test_key_is_offset_independent(self):
        """The same instant expressed in another offset yields the same key."""
        import uuid

        from datetime import timezone

        from app.services.notification import NotificationService as NS

        reminder_id = uuid.uuid4()
        utc_value = utcnow().replace(microsecond=0)
        shifted = utc_value.astimezone(timezone(timedelta(hours=5, minutes=30)))
        assert (
            NS.build_idempotency_key(reminder_id, utc_value, DeliveryChannel.console)
            == NS.build_idempotency_key(
                reminder_id, shifted, DeliveryChannel.console
            )
        )

    def test_materialising_twice_creates_one_row(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        occurrence = reminder.next_occurrence_at

        first, created_first = service.materialize(
            reminder, occurrence_at=occurrence
        )
        second, created_second = service.materialize(
            reminder, occurrence_at=occurrence
        )

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert len(service.history_for_patient(reminder.patient_id)) == 1

    def test_database_rejects_a_duplicate_key_even_on_a_direct_insert(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        The unique index - not the application check - is the real guarantee.

        Bypassing the service and inserting the same key twice must fail at the
        database, which is what protects against two workers racing.
        """
        from sqlalchemy.exc import IntegrityError

        from app.models.notification import Notification

        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        db_session.commit()

        clone = Notification(
            patient_id=notification.patient_id,
            reminder_id=notification.reminder_id,
            notification_type=notification.notification_type,
            scheduled_for=notification.scheduled_for,
            timezone=notification.timezone,
            status=NotificationStatus.pending,
            channel=notification.channel,
            body="duplicate",
            recipient=notification.recipient,
            idempotency_key=notification.idempotency_key,
        )
        db_session.add(clone)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_materialised_row_carries_the_source_reference(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient, medication, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        assert notification.patient_id == patient.id
        assert notification.reminder_id == reminder.id
        assert notification.medication_id == medication.id
        assert notification.appointment_id is None
        assert notification.notification_type == NotificationType.medication_reminder
        assert notification.status == NotificationStatus.pending
        assert notification.attempt_count == 0

    def test_recipient_comes_from_the_patient_not_the_reminder(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication, name="Rec"
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        assert notification.recipient == patient.contact_number

    def test_patient_without_any_number_fails_to_materialise(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        A reminder nobody can receive is not a reminder, so this is refused
        rather than silently addressed to a blank string.
        """
        patient = make_patient()
        patient.timezone = "UTC"
        patient.contact_number = ""
        patient.caregiver_contact = None
        phase5_services["db"].flush()
        from datetime import time

        from app.schemas.reminder import MedicationReminderCreate

        medication = make_medication(patient)
        reminder = phase5_services["reminders"].create_medication_reminder(
            patient.id,
            MedicationReminderCreate(medication_id=medication.id, times=[time(8, 0)]),
        )[0]
        with pytest.raises(NotificationPermanentError, match="no contact number"):
            phase5_services["notifications"].materialize(
                reminder, occurrence_at=reminder.next_occurrence_at
            )


# ── Delivery ─────────────────────────────────────────────────────────────────


class TestDelivery:
    def test_successful_delivery_records_the_receipt(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )

        result = service.deliver(notification.id)
        assert result.status == NotificationStatus.sent
        assert result.sent_at is not None
        assert result.attempt_count == 1
        assert result.provider_message_id == "recorded-1"
        assert result.next_retry_at is None
        assert phase5_services["provider"].send_count == 1

    def test_delivering_twice_sends_once(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        The core no-duplicate-message property, from the delivery side.

        A redelivered task calls `deliver` again on an already-sent row; it must
        return the existing result without calling the provider.
        """
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )

        service.deliver(notification.id)
        again = service.deliver(notification.id)

        assert again.status == NotificationStatus.sent
        assert phase5_services["provider"].send_count == 1
        assert again.attempt_count == 1

    def test_body_contains_the_stored_dose_time_not_a_derived_one(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient, medication, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        # The medication's name and the clinician's time are present.
        assert medication.name in notification.body
        assert "08:00" in notification.body

    def test_unknown_notification_is_not_found(
        self, db_session, phase5_services
    ):
        import uuid

        from app.core.exceptions import NotificationNotFoundError

        with pytest.raises(NotificationNotFoundError):
            phase5_services["notifications"].get(uuid.uuid4())


# ── Retry policy ─────────────────────────────────────────────────────────────


def _failing_service(db, settings, provider):
    return NotificationService(db, settings=settings, provider=provider)


def _elapse_backoff(db, notification_id):
    """
    Move a notification's `next_retry_at` into the past.

    `deliver` refuses to claim a row whose backoff has not elapsed - that is the
    point of the backoff, and `phase5_settings` uses a 1-second delay, so a
    test cannot simply sleep.  Ageing the column models what the passage of time
    does in production, and keeps the suite fast and deterministic.
    """
    from sqlalchemy import text

    db.execute(
        text("UPDATE notifications SET next_retry_at = :when WHERE id = :nid"),
        {"when": utcnow() - timedelta(seconds=5), "nid": str(notification_id)},
    )
    db.commit()
    db.expire_all()


class TestRetryPolicy:
    def test_transient_failure_is_scheduled_for_retry(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )

        flaky = RecordingNotificationProvider(
            fail_times=99, transient_error=NotificationTransientError("timeout")
        )
        failing = _failing_service(db_session, phase5_services["settings"], flaky)

        result = failing.deliver(notification.id)
        assert result.status == NotificationStatus.failed
        # Still retryable: a backoff time is set.
        assert result.next_retry_at is not None
        assert result.next_retry_at > utcnow()
        assert result.attempt_count == 1

    def test_permanent_failure_is_never_scheduled_for_retry(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        notification, _ = phase5_services["notifications"].materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )

        broken = RecordingNotificationProvider(
            permanent_error=NotificationPermanentError("rejected")
        )
        failing = _failing_service(db_session, phase5_services["settings"], broken)

        result = failing.deliver(notification.id)
        assert result.status == NotificationStatus.failed
        # No next_retry_at is what makes it permanently ineligible.
        assert result.next_retry_at is None

    def test_retry_eventually_succeeds_and_clears_the_error(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )

        # Fail twice, succeed on the third attempt.
        flaky = RecordingNotificationProvider(
            fail_times=2, transient_error=NotificationTransientError("temporary")
        )
        failing = _failing_service(db_session, phase5_services["settings"], flaky)

        first = failing.deliver(notification.id)
        assert first.status == NotificationStatus.failed
        _elapse_backoff(db_session, notification.id)
        second = failing.deliver(notification.id)
        assert second.status == NotificationStatus.failed
        _elapse_backoff(db_session, notification.id)
        third = failing.deliver(notification.id)
        assert third.status == NotificationStatus.sent
        assert third.sent_at is not None
        assert third.last_error is None
        assert third.next_retry_at is None
        assert third.attempt_count == 3
        # Three transport calls, but still ONE notification row.
        assert len(failing.history_for_patient(reminder.patient_id)) == 1

    def test_backoff_window_is_respected_between_attempts(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        A retry before the backoff elapses is refused.

        Without this, a misbehaving provider would be hammered on every tick
        instead of being given room to recover.
        """
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        notification, _ = phase5_services["notifications"].materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        flaky = RecordingNotificationProvider(
            fail_times=99, transient_error=NotificationTransientError("temporary")
        )
        failing = _failing_service(db_session, phase5_services["settings"], flaky)

        failing.deliver(notification.id)
        # Still inside the 1-second window: no second transport call.
        immediate = failing.deliver(notification.id)
        assert immediate.attempt_count == 1
        assert flaky.send_count == 1

    def test_retry_budget_is_finite(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        A permanently broken endpoint must stop, not loop forever.

        `phase5_settings` allows 3 attempts, so the 4th must not be scheduled.
        """
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        notification, _ = phase5_services["notifications"].materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        broken = RecordingNotificationProvider(
            fail_times=99, transient_error=NotificationTransientError("down")
        )
        failing = _failing_service(db_session, phase5_services["settings"], broken)

        for expected_attempt in (1, 2, 3):
            result = failing.deliver(notification.id)
            assert result.attempt_count == expected_attempt
            _elapse_backoff(db_session, notification.id)

        # Budget exhausted: a 4th failure is recorded but never rescheduled.
        final = failing.deliver(notification.id)
        assert final.attempt_count == 4
        assert final.next_retry_at is None
        assert final.status == NotificationStatus.failed

    def test_backoff_grows_and_is_clamped(
        self, db_session, make_patient, make_medication, phase5_settings
    ):
        """Delays double per attempt, up to the configured ceiling."""
        service = NotificationService(
            db_session, settings=phase5_settings, provider=RecordingNotificationProvider()
        )
        base = phase5_settings.notification_retry_base_seconds  # 1
        ceiling = phase5_settings.notification_retry_max_seconds  # 4
        assert service._backoff_seconds(1) == base
        assert service._backoff_seconds(2) == base * 2
        assert service._backoff_seconds(3) == base * 4
        # Clamped: an attempt far out must not schedule hours ahead.
        assert service._backoff_seconds(30) == ceiling

    def test_retry_due_picks_up_only_eligible_rows(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        notification, _ = phase5_services["notifications"].materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        flaky = RecordingNotificationProvider(
            fail_times=1, transient_error=NotificationTransientError("temporary")
        )
        failing = _failing_service(db_session, phase5_services["settings"], flaky)

        failing.deliver(notification.id)
        # Not yet due: the backoff has not elapsed.
        assert failing.retry_due() == {"examined": 0, "sent": 0, "failed": 0}

        # Age the backoff into the past.
        db_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE notifications SET next_retry_at = :when WHERE id = :nid"
            ),
            {"when": utcnow() - timedelta(minutes=5), "nid": str(notification.id)},
        )
        db_session.commit()
        db_session.expire_all()

        outcome = failing.retry_due()
        assert outcome == {"examined": 1, "sent": 1, "failed": 0}

    def test_permanently_failed_rows_are_never_picked_up(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        notification, _ = phase5_services["notifications"].materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        broken = RecordingNotificationProvider(
            permanent_error=NotificationPermanentError("rejected")
        )
        failing = _failing_service(db_session, phase5_services["settings"], broken)
        failing.deliver(notification.id)
        assert failing.retry_due() == {"examined": 0, "sent": 0, "failed": 0}


# ── Secret hygiene ───────────────────────────────────────────────────────────


class TestErrorRedaction:
    def test_provider_error_detail_is_redacted_before_storage(
        self, db_session, make_patient, make_medication
    ):
        """
        A vendor error can echo the access token, so the stored detail must be
        passed through redaction first.
        """
        from datetime import time

        from app.core.config import get_settings
        from app.schemas.reminder import MedicationReminderCreate
        from app.services.reminder import ReminderService

        secret = "tok_supersecretvalue123"
        settings_with_secret = get_settings().model_copy(
            update={"whatsapp_access_token": secret}
        )

        patient = make_patient()
        patient.timezone = "UTC"
        db_session.flush()
        medication = make_medication(patient)
        reminder = ReminderService(db_session).create_medication_reminder(
            patient.id,
            MedicationReminderCreate(medication_id=medication.id, times=[time(8, 0)]),
        )[0]

        leaky = RecordingNotificationProvider(
            fail_times=1,
            transient_error=NotificationTransientError(
                f"vendor rejected token {secret}"
            ),
        )
        service = NotificationService(
            db_session, settings=settings_with_secret, provider=leaky
        )
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        result = service.deliver(notification.id)

        assert secret not in (result.last_error or "")
        assert "[redacted]" in (result.last_error or "")

    def test_error_detail_is_length_bounded(
        self, db_session, phase5_settings
    ):
        """A huge vendor payload must not be copied into the row."""
        service = NotificationService(
            db_session,
            settings=phase5_settings,
            provider=RecordingNotificationProvider(),
        )
        huge = NotificationPermanentError("x" * 50_000)
        assert len(service._safe_error(huge)) <= 1000


# ── Orphan recovery ──────────────────────────────────────────────────────────


class TestStaleReclaim:
    def test_sending_row_orphaned_by_a_dead_worker_is_reclaimed(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        A worker killed mid-send leaves a row in `sending` forever.  Without
        reclamation the patient would silently stop receiving reminders.
        """
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        service = phase5_services["notifications"]
        notification, _ = service.materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )

        # Force the row into the `sending` state a crashed worker would leave.
        db_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE notifications SET status='sending', attempt_count=1, "
                "updated_at = :when WHERE id = :nid"
            ),
            {
                "when": utcnow() - timedelta(seconds=STALE_SENDING_AFTER_SECONDS + 60),
                "nid": str(notification.id),
            },
        )
        db_session.commit()
        db_session.expire_all()

        assert service.reclaim_stale() == 1
        reloaded = service.get(notification.id)
        assert reloaded.status == NotificationStatus.pending

        # And it can now be delivered.
        assert service.deliver(notification.id).status == NotificationStatus.sent

    def test_a_live_slow_delivery_is_not_stolen(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        Reclamation is bounded by age so a slow-but-healthy delivery is not
        handed to a second worker while the first is still talking to the API.
        """
        _, _, reminder = _medication_reminder(
            phase5_services, make_patient, make_medication
        )
        notification, _ = phase5_services["notifications"].materialize(
            reminder, occurrence_at=reminder.next_occurrence_at
        )
        db_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE notifications SET status='sending', updated_at = :when "
                "WHERE id = :nid"
            ),
            {"when": utcnow(), "nid": str(notification.id)},
        )
        db_session.commit()
        db_session.expire_all()

        assert phase5_services["notifications"].reclaim_stale() == 0


# ── Provider contract ────────────────────────────────────────────────────────


class TestProviderConfiguration:
    def test_console_provider_is_the_safe_default(self, phase5_settings):
        assert phase5_settings.notification_provider == "console"
        provider = RecordingNotificationProvider()
        assert provider.is_configured()

    def test_unconfigured_provider_fails_at_send_not_construction(
        self, db_session, phase5_settings
    ):
        """
        A half-configured deployment must degrade one delivery, not prevent the
        worker from booting - so the error surfaces on send.
        """
        from app.notifications.whatsapp import WhatsAppNotificationProvider

        provider = WhatsAppNotificationProvider(phase5_settings)
        assert provider.is_configured() is False
        with pytest.raises(NotificationProviderNotConfiguredError):
            provider.send(recipient="+15551234567", body="test")

    def test_channel_must_be_a_known_delivery_channel(
        self, db_session, phase5_settings
    ):
        class RogueProvider(NotificationProvider):
            channel = "smoke-signal"

            def is_configured(self):
                return True

            def send(self, *, recipient, body):
                raise AssertionError("should not reach the transport")

        service = NotificationService(
            db_session, settings=phase5_settings, provider=RogueProvider()
        )
        with pytest.raises(NotificationPermanentError, match="not a known"):
            service._channel()
