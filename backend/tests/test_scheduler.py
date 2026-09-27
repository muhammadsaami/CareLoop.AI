"""
CareLoop AI — Scheduler Tests (Phase 5)

The recurring-job engine.  The two properties that matter most:

  * dispatch is IDEMPOTENT - running the scan repeatedly over the same window
    never creates a second notification for an occurrence,
  * OVERDUE occurrences are not sent - a medication reminder that fires hours
    late is a safety problem, because the dose has probably already been taken
    and a late prompt risks a double dose.

Also covered: one-shot appointment reminders completing, the due window's upper
bound, and a bad reminder not starving the rest of the batch.
"""
from datetime import time, timedelta

import pytest
from sqlalchemy import text

from app.core.timezones import to_local, utcnow
from app.models.notification import NotificationStatus
from app.models.reminder import ReminderStatus
from app.schemas.reminder import AppointmentReminderCreate, MedicationReminderCreate


def _set_next_occurrence(db, reminder_id, when):
    """
    Force a reminder's stored next occurrence, and return that instant.

    The return value matters: `dispatch_due(now=...)` defaults to a freshly
    computed `utcnow()`, which is a few microseconds later than the value the
    test stamps. The due window is inclusive of `now`, so stamping `utcnow()`
    and then dispatching would put the occurrence *just* before the window and
    silently scan nothing.  Callers pass the returned value back as `now` so the
    window is evaluated against the exact instant that was stored.
    """
    db.execute(
        text("UPDATE reminders SET next_occurrence_at = :when WHERE id = :rid"),
        {"when": when, "rid": str(reminder_id)},
    )
    db.commit()
    db.expire_all()
    return when


def _set_reminder_status(db, reminder_id, status):
    db.execute(
        text("UPDATE reminders SET status = :status WHERE id = :rid"),
        {"status": status, "rid": str(reminder_id)},
    )
    db.commit()
    db.expire_all()


#: How far ahead of now a "due" occurrence is stamped.  It must be positive and
#: comfortably smaller than the 300s dispatch window, so the stored instant is
#: unambiguously inside the inclusive [now, horizon] range that `find_due`
#: scans.  Stamping exactly `now` would not work: the dispatch call computes its
#: own `utcnow()` microseconds later, putting the occurrence just *before* the
#: window and scanning nothing.
_DUE_OFFSET = timedelta(seconds=5)


def _stamp_due(db, reminder_id):
    """Mark a reminder due now, and return the instant it was stamped with."""
    return _set_next_occurrence(
        db, reminder_id, utcnow() + _DUE_OFFSET
    )


def _medication_fixture(phase5_services, make_patient, make_medication, times=("08:00",), **patient_kwargs):
    from app.services.reminder import ReminderService

    patient = make_patient(**patient_kwargs)
    patient.timezone = "UTC"
    phase5_services["db"].flush()
    medication = make_medication(patient)
    reminders = ReminderService(phase5_services["db"]).create_medication_reminder(
        patient.id,
        MedicationReminderCreate(
            medication_id=medication.id,
            times=[time.fromisoformat(t) for t in times],
        ),
    )
    return patient, medication, reminders


# ── Idempotent dispatch ──────────────────────────────────────────────────────


class TestDispatchIdempotency:
    def test_due_reminder_is_materialised(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _stamp_due(db_session, reminder.id)
        result = phase5_services["scheduler"].dispatch_due()
        assert result.scanned == 1
        assert result.materialized == 1
        assert result.skipped_duplicate == 0

        notifications = phase5_services["notifications"].history_for_patient(
            patient.id
        )
        assert len(notifications) == 1
        assert notifications[0].status == NotificationStatus.pending

    def test_repeated_dispatch_creates_no_duplicate(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        The guarantee, run five times.

        After the first pass the rule's `next_occurrence_at` has moved forward,
        so later passes would not see it anyway - but the idempotency key is
        what protects the case where they do (a transaction that did not commit
        the advance, or two workers running concurrently).
        """
        patient, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        due_at = _stamp_due(db_session, reminder.id)

        first = phase5_services["scheduler"].dispatch_due()
        assert first.materialized == 1

        # Re-pin the SAME occurrence so each later pass definitely considers it -
        # this is the race the idempotency key exists to resolve.
        for _ in range(4):
            _set_next_occurrence(db_session, reminder.id, due_at)
            result = phase5_services["scheduler"].dispatch_due()
            assert result.materialized == 0
            assert result.skipped_duplicate == 1

        notifications = phase5_services["notifications"].history_for_patient(
            patient.id
        )
        assert len(notifications) == 1

    def test_dispatch_never_sends_on_its_own(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        Materialising and delivering are separate steps on separate cadences.

        Dispatch writes a `pending` row; the delivery task is what calls the
        provider.  Conflating them would make a scan depend on vendor latency.
        """
        _, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _stamp_due(db_session, reminder.id)

        phase5_services["scheduler"].dispatch_due()
        assert phase5_services["provider"].send_count == 0


# ── Recurrence advancement ───────────────────────────────────────────────────


class TestRecurrenceAdvancement:
    def test_rule_advances_to_the_next_day(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        due_at = _stamp_due(db_session, reminder.id)

        phase5_services["scheduler"].dispatch_due()

        db_session.expire_all()
        reloaded = phase5_services["reminders"].get(reminder.id)
        # Advanced strictly beyond the occurrence that was just handled.
        assert reloaded.next_occurrence_at > due_at
        # Still 08:00 local - advancing must not drift the wall-clock time.
        assert (
            to_local(reloaded.next_occurrence_at, reloaded.timezone).strftime("%H:%M")
            == "08:00"
        )

    def test_each_dose_time_advances_independently(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        Two reminders for one medication are two independent rules, so one
        firing does not disturb the other's schedule.
        """
        _, _, reminders = _medication_fixture(
            phase5_services, make_patient, make_medication, times=("08:00", "20:00")
        )
        morning, evening = reminders

        _set_next_occurrence(db_session, morning.id, utcnow())
        phase5_services["scheduler"].dispatch_due()
        db_session.expire_all()

        # The evening rule is untouched by the morning one firing.
        assert (
            phase5_services["reminders"].get(evening.id).next_occurrence_at
            == evening.next_occurrence_at
        )

    def test_paused_reminder_is_never_dispatched(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _stamp_due(db_session, reminder.id)
        _set_reminder_status(db_session, reminder.id, ReminderStatus.paused.value)

        result = phase5_services["scheduler"].dispatch_due()
        assert result.scanned == 0
        assert phase5_services["notifications"].history_for_patient(patient.id) == []

    def test_cancelled_reminder_is_never_dispatched(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _stamp_due(db_session, reminder.id)
        _set_reminder_status(db_session, reminder.id, ReminderStatus.cancelled.value)

        assert phase5_services["scheduler"].dispatch_due().scanned == 0
        assert phase5_services["notifications"].history_for_patient(patient.id) == []


# ── The due window ───────────────────────────────────────────────────────────


class TestDueWindow:
    def test_occurrence_outside_the_window_is_not_due(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        # An hour out, but the window is 300s.
        _set_next_occurrence(
            db_session, reminder.id, utcnow() + timedelta(hours=1)
        )
        assert phase5_services["scheduler"].dispatch_due().scanned == 0

    def test_occurrence_inside_the_window_is_due(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _set_next_occurrence(
            db_session, reminder.id, utcnow() + timedelta(seconds=60)
        )
        assert phase5_services["scheduler"].dispatch_due().scanned == 1

    def test_window_can_be_overridden_per_call(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _set_next_occurrence(
            db_session, reminder.id, utcnow() + timedelta(hours=2)
        )
        assert (
            phase5_services["scheduler"].dispatch_due(
                look_ahead_seconds=7200
            ).scanned
            == 1
        )

    def test_batch_size_is_respected(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, reminders = _medication_fixture(
            phase5_services, make_patient, make_medication, times=("08:00", "12:00", "20:00")
        )
        for reminder in reminders:
            _stamp_due(db_session, reminder.id)

        assert phase5_services["scheduler"].dispatch_due(limit=2).scanned == 2


# ── Overdue: the safety-critical case ────────────────────────────────────────


class TestOverdueOccurrences:
    def test_overdue_is_not_dispatched_by_the_due_scan(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        The due window starts at `now`, so a stale occurrence is never picked
        up as if it were current.
        """
        patient, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _set_next_occurrence(
            db_session, reminder.id, utcnow() - timedelta(hours=3)
        )
        result = phase5_services["scheduler"].dispatch_due()
        assert result.scanned == 0
        assert phase5_services["notifications"].history_for_patient(patient.id) == []

    def test_skip_overdue_advances_without_sending(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        The point of `skip_overdue`: retire the missed occurrence and move to the
        next real one, without a stale clinical message ever being sent.
        """
        patient, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _set_next_occurrence(
            db_session, reminder.id, utcnow() - timedelta(hours=3)
        )

        assert phase5_services["scheduler"].skip_overdue() == 1
        assert phase5_services["provider"].send_count == 0
        # No notification row was created for the missed dose.
        assert phase5_services["notifications"].history_for_patient(patient.id) == []

        db_session.expire_all()
        reloaded = phase5_services["reminders"].get(reminder.id)
        assert reloaded.next_occurrence_at > utcnow()
        assert reloaded.status == ReminderStatus.active

    def test_repeated_reconcile_does_not_loop(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _set_next_occurrence(
            db_session, reminder.id, utcnow() - timedelta(days=10)
        )
        # First pass advances it; the second has nothing left to skip.
        assert phase5_services["scheduler"].skip_overdue() == 1
        assert phase5_services["scheduler"].skip_overdue() == 0

    def test_end_date_retires_the_rule_instead_of_firing_forever(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient = make_patient()
        patient.timezone = "UTC"
        db_session.flush()
        medication = make_medication(patient)
        from app.services.reminder import ReminderService

        # A one-day course: window closes 10 minutes from now.
        reminder = ReminderService(db_session).create_medication_reminder(
            patient.id,
            MedicationReminderCreate(
                medication_id=medication.id,
                times=[time(8, 0)],
                end_date=utcnow() + timedelta(minutes=10),
            ),
        )[0]
        _stamp_due(db_session, reminder.id)

        phase5_services["scheduler"].dispatch_due()
        db_session.expire_all()
        reloaded = phase5_services["reminders"].get(reminder.id)
        assert reloaded.status == ReminderStatus.completed
        assert reloaded.next_occurrence_at is None


# ── One-shot appointment reminders ───────────────────────────────────────────


class TestAppointmentDispatch:
    def _appointment_reminder(self, phase5_services, make_patient, make_appointment):
        from app.services.reminder import ReminderService

        patient = make_patient()
        patient.timezone = "UTC"
        phase5_services["db"].flush()
        appointment = make_appointment(
            patient, when=utcnow() + timedelta(days=2)
        )
        reminder = ReminderService(phase5_services["db"]).create_appointment_reminder(
            patient.id,
            AppointmentReminderCreate(
                appointment_id=appointment.id, lead_time_minutes=60
            ),
        )
        return patient, appointment, reminder

    def test_appointment_reminder_fires_and_completes(
        self, db_session, make_patient, make_appointment, phase5_services
    ):
        """
        An appointment happens once, so the rule is retired after it fires -
        there is no recurrence to advance to.
        """
        patient, appointment, reminder = self._appointment_reminder(
            phase5_services, make_patient, make_appointment
        )
        _stamp_due(db_session, reminder.id)
        result = phase5_services["scheduler"].dispatch_due()
        assert result.materialized == 1
        # A one-shot appointment rule retires on the occurrence it fires for.
        assert result.completed == 1

        db_session.expire_all()
        reloaded = phase5_services["reminders"].get(reminder.id)
        assert reloaded.status == ReminderStatus.completed
        assert reloaded.next_occurrence_at is None

        notifications = phase5_services["notifications"].history_for_patient(
            patient.id
        )
        assert len(notifications) == 1
        assert notifications[0].appointment_id == appointment.id
        assert notifications[0].medication_id is None

    def test_completed_appointment_reminder_is_not_dispatched_again(
        self, db_session, make_patient, make_appointment, phase5_services
    ):
        patient, _, reminder = self._appointment_reminder(
            phase5_services, make_patient, make_appointment
        )
        _stamp_due(db_session, reminder.id)
        phase5_services["scheduler"].dispatch_due()

        # Even if the occurrence were somehow still stamped, the status stops it.
        assert phase5_services["scheduler"].dispatch_due().scanned == 0
        assert len(
            phase5_services["notifications"].history_for_patient(patient.id)
        ) == 1


# ── Delivery ─────────────────────────────────────────────────────────────────


class TestDeliveryPass:
    def test_pending_notifications_are_delivered(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _stamp_due(db_session, reminder.id)
        phase5_services["scheduler"].dispatch_due()

        # The occurrence is stamped a few seconds ahead so the dispatch window
        # catches it; delivery needs the clock to have reached it.
        outcome = phase5_services["scheduler"].deliver_pending(
            now=utcnow() + _DUE_OFFSET + timedelta(seconds=1)
        )
        assert outcome["sent"] == 1
        assert phase5_services["provider"].send_count == 1

    def test_delivery_pass_is_idempotent(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        _stamp_due(db_session, reminder.id)
        phase5_services["scheduler"].dispatch_due()
        when_due = utcnow() + _DUE_OFFSET + timedelta(seconds=1)

        phase5_services["scheduler"].deliver_pending(now=when_due)
        second = phase5_services["scheduler"].deliver_pending(now=when_due)
        assert second["sent"] == 0
        # The provider was called exactly once in total.
        assert phase5_services["provider"].send_count == 1

    def test_not_yet_due_notifications_are_not_delivered(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, _, (reminder,) = _medication_fixture(
            phase5_services, make_patient, make_medication
        )
        # Future occurrence: dispatch it, but it must not send yet.
        _set_next_occurrence(
            db_session, reminder.id, utcnow() + timedelta(seconds=30)
        )
        phase5_services["scheduler"].dispatch_due()

        assert phase5_services["scheduler"].deliver_pending()["sent"] == 0
        assert phase5_services["provider"].send_count == 0


# ── Batch resilience ─────────────────────────────────────────────────────────


class TestBatchResilience:
    def test_one_undeliverable_reminder_does_not_starve_the_batch(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        A patient with no reachable number must not block everyone else's
        reminders, and must not be retried forever on every tick.
        """
        from app.services.reminder import ReminderService

        broken_patient = make_patient(name="No Number", suffix="1101")
        broken_patient.timezone = "UTC"
        broken_patient.contact_number = ""
        broken_patient.caregiver_contact = None
        db_session.flush()
        broken_medication = make_medication(broken_patient)

        healthy_patient = make_patient(name="Healthy", suffix="1102")
        healthy_patient.timezone = "UTC"
        db_session.flush()
        healthy_medication = make_medication(healthy_patient)

        service = ReminderService(db_session)
        broken = service.create_medication_reminder(
            broken_patient.id,
            MedicationReminderCreate(
                medication_id=broken_medication.id, times=[time(8, 0)]
            ),
        )[0]
        healthy = service.create_medication_reminder(
            healthy_patient.id,
            MedicationReminderCreate(
                medication_id=healthy_medication.id, times=[time(8, 0)]
            ),
        )[0]
        _stamp_due(db_session, broken.id)
        _stamp_due(db_session, healthy.id)

        result = phase5_services["scheduler"].dispatch_due()
        # Both were scanned, and the healthy one still got its notification.
        assert result.scanned == 2
        # Exactly one failed, and it was isolated to its own savepoint.
        assert result.failed == 1
        assert result.materialized == 1
        # The healthy one still got its notification.
        assert len(
            phase5_services["notifications"].history_for_patient(healthy_patient.id)
        ) == 1
        # The broken one produced nothing but was advanced, so it will not
        # raise an exception on every future tick.
        assert (
            phase5_services["notifications"].history_for_patient(broken_patient.id)
            == []
        )
        db_session.expire_all()
        assert phase5_services["reminders"].get(broken.id).next_occurrence_at > utcnow()
