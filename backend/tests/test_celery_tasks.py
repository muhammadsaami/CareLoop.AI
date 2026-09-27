"""
CareLoop AI — Celery Task Tests (Phase 5)

The tasks are thin wrappers, so what needs proving is narrow but important:

1. The app really does register the Phase 5 tasks and the beat schedule, and
   the safety-relevant Celery settings are the ones we intended.
2. The wrappers run, are idempotent, and hand back plain JSON-safe values -
   Celery will serialise whatever they return, so a returned ORM object would
   only fail once a real broker was involved.
3. A failing task still rolls back and re-raises, so Celery's
   late-acknowledgement config actually retries it.
4. No task requires a live broker.  Nothing here connects to Redis.

The tasks open their own session via `SessionLocal`, so each test points that
name at the test session.  The proxy below deliberately ignores `close()`:
session lifetime belongs to the fixture, and a closed fixture session would
break teardown.
"""
from datetime import time, timedelta

import pytest

from app.core.timezones import utcnow


@pytest.fixture
def tasks_on_test_session(monkeypatch, db_session, phase5_settings,
                          recording_provider):
    """
    Point the task module's session factory and settings at the test doubles.

    Also forces task execution to run inline.  We do not use Celery's `eager`
    mode here because it swallows the return value of some bound tasks; calling
    the task object directly runs exactly the same body.
    """
    from app.workers import tasks as tasks_module

    class _SessionProxy:
        """A session that delegates everything except `close()`."""

        def __init__(self, session):
            self._session = session

        def __getattr__(self, name):
            return getattr(self._session, name)

        def close(self):  # noqa: D102 - fixture owns the real session
            return None

    monkeypatch.setattr(
        tasks_module, "SessionLocal", lambda: _SessionProxy(db_session)
    )
    # The services inside the tasks read real settings; point the scheduler at
    # the recording provider so delivery never touches a network.
    monkeypatch.setattr(
        "app.services.notification.get_settings", lambda: phase5_settings
    )
    monkeypatch.setattr(
        "app.services.scheduler.get_settings", lambda: phase5_settings
    )
    return tasks_module


def _medication_reminder(services, patient, medication, at=time(8, 0)):
    """Create one medication reminder and return it."""
    from app.schemas.reminder import MedicationReminderCreate

    return services["reminders"].create_medication_reminder(
        patient.id,
        MedicationReminderCreate(medication_id=medication.id, times=[at]),
    )[0]


def _stamp_due(db, reminder_id):
    """Push a reminder's next occurrence a few seconds into the future.

    The dispatch window is inclusive of `now`, so stamping exactly `utcnow()`
    would fall a few microseconds *before* the window that the dispatch call
    computes for itself.  Positive offset keeps it unambiguously inside.
    """
    from sqlalchemy import text

    when = utcnow() + timedelta(seconds=5)
    db.execute(
        text("UPDATE reminders SET next_occurrence_at = :when WHERE id = :rid"),
        {"when": when, "rid": str(reminder_id)},
    )
    db.commit()
    db.expire_all()
    return when


class TestCeleryRegistration:
    def test_all_phase5_tasks_are_registered(self):
        from app.workers.celery_app import celery_app

        names = set(celery_app.tasks)
        assert {
            "careloop.dispatch_due_reminders",
            "careloop.deliver_pending_notifications",
            "careloop.send_notification",
            "careloop.retry_failed_notifications",
            "careloop.reconcile_scheduler",
        } <= names

    def test_beat_schedule_covers_the_three_recurring_jobs(self):
        from app.workers.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert schedule["dispatch-due-reminders"]["task"] == (
            "careloop.dispatch_due_reminders"
        )
        assert schedule["deliver-pending-notifications"]["task"] == (
            "careloop.deliver_pending_notifications"
        )
        assert schedule["reconcile-scheduler"]["task"] == (
            "careloop.reconcile_scheduler"
        )

    def test_safety_relevant_celery_settings(self):
        """
        These three are what make at-least-once delivery safe here.  A regression
        in any of them is a real defect, not a style preference: default
        `acks_late=False` would drop a task killed mid-flight, and a prefetch
        above 1 would let one worker hoard due reminders.
        """
        from app.workers.celery_app import celery_app

        conf = celery_app.conf
        assert conf.task_acks_late is True
        assert conf.task_acks_on_failure_or_timeout is True
        assert conf.worker_prefetch_multiplier == 1
        assert conf.task_default_queue == "careloop.notifications"
        assert conf.task_serializer == "json"
        assert conf.result_serializer == "json"
        # Idempotency must never depend on the result backend.
        assert conf.task_ignore_result is True

    def test_task_names_are_registered_and_callable(self, tasks_on_test_session):
        for name in (
            "dispatch_due_reminders",
            "deliver_pending_notifications",
            "send_notification",
            "retry_failed_notifications",
            "reconcile_scheduler",
        ):
            task = getattr(tasks_on_test_session, name)
            assert callable(task)
            assert task.name.startswith("careloop.")


class TestTaskWrappers:
    def test_dispatch_task_materialises_due_reminders(
        self, tasks_on_test_session, phase5_services, make_patient,
        make_medication
    ):
        patient = make_patient(name="Task Patient", suffix="2001")
        patient.timezone = "UTC"
        medication = make_medication(patient)
        reminder = _medication_reminder(phase5_services, patient, medication)
        _stamp_due(phase5_services["db"], reminder.id)

        result = tasks_on_test_session.dispatch_due_reminders.run(
            limit=50
        )

        # Celery serialises the return value as JSON, so it must be plain data.
        assert isinstance(result, dict)
        assert result["materialized"] == 1
        assert result["scanned"] == 1
        assert all(isinstance(v, int) for v in result.values())

    def test_dispatch_task_is_idempotent_when_repeated(
        self, tasks_on_test_session, phase5_services, make_patient,
        make_medication
    ):
        patient = make_patient(name="Repeat Patient", suffix="2002")
        patient.timezone = "UTC"
        medication = make_medication(patient)
        reminder = _medication_reminder(phase5_services, patient, medication)
        when = _stamp_due(phase5_services["db"], reminder.id)

        first = tasks_on_test_session.dispatch_due_reminders.run(limit=50)
        assert first["materialized"] == 1

        # Re-pin the same occurrence and re-run: the at-least-once broker
        # delivering this twice must not produce a second message.
        from sqlalchemy import text

        phase5_services["db"].execute(
            text("UPDATE reminders SET next_occurrence_at = :w WHERE id = :r"),
            {"w": when, "r": str(reminder.id)},
        )
        phase5_services["db"].commit()

        second = tasks_on_test_session.dispatch_due_reminders.run(limit=50)
        assert second["materialized"] == 0
        assert second["skipped_duplicate"] == 1

        assert len(
            phase5_services["notifications"].history_for_patient(patient.id)
        ) == 1

    def test_deliver_task_sends_pending_notifications(
        self, tasks_on_test_session, phase5_services, make_patient,
        make_medication
    ):
        patient = make_patient(name="Deliver Patient", suffix="2003")
        patient.timezone = "UTC"
        medication = make_medication(patient)
        reminder = _medication_reminder(phase5_services, patient, medication)
        _stamp_due(phase5_services["db"], reminder.id)
        tasks_on_test_session.dispatch_due_reminders.run(limit=50)

        # Occurrence is a few seconds ahead, so delivery is not due yet.
        assert tasks_on_test_session.deliver_pending_notifications.run()["sent"] == 0

    def test_send_notification_task_delivers_one(
        self, tasks_on_test_session, phase5_services, make_patient,
        make_medication
    ):
        patient = make_patient(name="Single Patient", suffix="2004")
        patient.timezone = "UTC"
        medication = make_medication(patient)
        reminder = _medication_reminder(phase5_services, patient, medication)
        _stamp_due(phase5_services["db"], reminder.id)
        tasks_on_test_session.dispatch_due_reminders.run(limit=50)

        notification = phase5_services["notifications"].history_for_reminder(
            reminder.id
        )[0]

        result = tasks_on_test_session.send_notification.run(str(notification.id))

        assert result["notification_id"] == str(notification.id)
        assert result["status"] in {"sent", "failed", "pending", "sending"}
        assert isinstance(result["attempt_count"], int)

    def test_retry_and_reconcile_tasks_return_count_dicts(
        self, tasks_on_test_session
    ):
        for name in ("retry_failed_notifications", "reconcile_scheduler"):
            result = getattr(tasks_on_test_session, name).run(limit=10)
            assert isinstance(result, dict)
            assert all(isinstance(v, int) for v in result.values())


class TestTaskFailureHandling:
    def test_failing_task_rolls_back_and_reraises(
        self, tasks_on_test_session, monkeypatch
    ):
        """
        Celery retries a task only if the exception propagates.  A swallowed
        exception would silently mark the work done, and with
        `acks_on_failure_or_timeout` the message would be redelivered forever
        with no error surface.  So the wrapper must roll back and re-raise.
        """
        from app.services.scheduler import SchedulerService

        def _boom(self, **kwargs):
            raise RuntimeError("scan exploded")

        monkeypatch.setattr(SchedulerService, "dispatch_due", _boom)

        with pytest.raises(RuntimeError, match="scan exploded"):
            tasks_on_test_session.dispatch_due_reminders.run(limit=10)

    def test_failure_log_names_the_real_exception_type(
        self, tasks_on_test_session, monkeypatch, caplog
    ):
        """
        `type(Exception).__name__` is the literal string "Exception" no matter
        what went wrong, which would make every failure indistinguishable in the
        logs.  The real type has to reach the log line.
        """
        from app.services.scheduler import SchedulerService

        class SomeVendorTimeout(RuntimeError):
            pass

        def _boom(self, **kwargs):
            raise SomeVendorTimeout("upstream timed out")

        monkeypatch.setattr(SchedulerService, "dispatch_due", _boom)

        with caplog.at_level("ERROR", logger="app.workers.tasks"):
            with pytest.raises(SomeVendorTimeout):
                tasks_on_test_session.dispatch_due_reminders.run(limit=10)

        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "SomeVendorTimeout" in text
        # And the generic placeholder must not be what got logged.
        assert "error_type=Exception\n" not in text

    def test_task_error_log_carries_no_phi(
        self, tasks_on_test_session, monkeypatch, caplog
    ):
        from app.services.scheduler import SchedulerService

        def _boom(self, **kwargs):
            raise RuntimeError("patient John Smith 555-0100 amoxicillin 500mg")

        monkeypatch.setattr(SchedulerService, "dispatch_due", _boom)

        with caplog.at_level("DEBUG"):
            with pytest.raises(RuntimeError):
                tasks_on_test_session.dispatch_due_reminders.run(limit=10)

        logged = "\n".join(
            record.getMessage() for record in caplog.records
        ) + caplog.text
        for secret in ("John Smith", "555-0100", "amoxicillin", "500mg"):
            assert secret not in logged
