"""
CareLoop AI — Phase 6 configuration and background recovery

Two things that are easy to get wrong and expensive to get wrong:

  * Configuration. A typo in a rule code, or enabling caregiver notifications
    while the escalation pathway is off, must be a startup error rather than a
    runtime surprise in front of a patient.
  * The recovery task. It runs every minute against a table that grows
    forever, so "does it stop, does it double-send, does it pick up what an
    inline request missed" are the questions that matter.
"""
import pytest

from app.core.config import Settings
from app.core.escalation_codes import (
    RULE_CODES,
    RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR,
    RULE_WARNING_SYMPTOM_WORSENED,
)
from app.models.notification import NotificationStatus
from app.models.reminder import ReminderStatus
from app.models.warning_symptom import SymptomSeverity


def _settings(**overrides) -> Settings:
    """
    Build settings from the DECLARED defaults, not from `.env`.

    `_env_file=None` matters: a developer's local `.env` has the pathway turned
    on, and a test asserting the shipped defaults would fail on their machine
    and pass in CI. These tests are about the defaults as declared.
    """
    base = {"checkin_escalation_enabled": True, "_env_file": None}
    base.update(overrides)
    return Settings(**base)


class TestCheckInSettings:
    def test_the_pathway_is_off_unless_it_is_turned_on(self):
        # Escalations are opt-in. A deployment that has not configured the
        # rules must not start raising them.
        #
        # Asserted on the declared field default, not on a constructed
        # Settings: `tests/conftest.py` loads `.env` into `os.environ`, and env
        # vars outrank `_env_file=None`, so a constructed instance reflects the
        # developer's local configuration rather than the shipped default.
        assert Settings.model_fields["checkin_escalation_enabled"].default is False

    def test_a_blank_rule_list_means_every_published_rule(self):
        # `None`, not an empty set. The distinction is deliberate: an operator
        # who deliberately configured zero rules has made a decision, while a
        # blank config means "you have not decided yet, use the reviewed set".
        # An empty list here would silently disable the whole pathway.
        assert _settings(checkin_enabled_rules="").checkin_active_rule_codes is None
        assert _settings(checkin_enabled_rules="   ").checkin_active_rule_codes is None

    def test_rules_can_be_named_explicitly(self):
        settings = _settings(
            checkin_enabled_rules=RULE_WARNING_SYMPTOM_WORSENED
        )
        assert settings.checkin_active_rule_codes == [
            RULE_WARNING_SYMPTOM_WORSENED
        ]

    def test_several_rules_can_be_listed(self):
        settings = _settings(
            checkin_enabled_rules=(
                f"{RULE_WARNING_SYMPTOM_WORSENED},"
                f"{RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR}"
            )
        )
        assert set(settings.checkin_active_rule_codes) == RULE_CODES

    def test_a_misspelled_rule_code_is_rejected_at_startup(self):
        # Not ignored: a deployment that believes a rule is on while it is off
        # is the worst possible failure, and it is invisible until an alert
        # fails to arrive.
        with pytest.raises(ValueError, match="NOT_A_RULE"):
            _settings(checkin_enabled_rules="NOT_A_RULE")

    @pytest.mark.parametrize("floor", ["low", "medium", "high", "critical"])
    def test_every_severity_is_a_valid_floor(self, floor):
        assert (
            _settings(checkin_severity_floor=floor).checkin_severity_floor_value
            == SymptomSeverity(floor)
        )

    def test_an_invalid_severity_floor_is_rejected(self):
        with pytest.raises(ValueError):
            _settings(checkin_severity_floor="extreme")

    def test_caregiver_notifications_require_the_pathway(self):
        # Asking for caregiver alerts while escalations are disabled is a
        # contradiction. Rejecting it means the deployment cannot believe
        # caregivers are being told about something the system never raises.
        with pytest.raises(ValueError, match="escalation"):
            _settings(
                checkin_escalation_enabled=False,
                checkin_notify_caregiver=True,
            )

    def test_caregiver_notifications_default_off(self):
        # Off by default even with the pathway on: sending a clinical alert to a
        # third party is not something to enable by accident.  Read from the
        # field default, for the reason given in the escalation-defaults test.
        assert Settings.model_fields["checkin_notify_caregiver"].default is False

    def test_an_invalid_prompt_time_is_rejected(self):
        with pytest.raises(ValueError):
            _settings(checkin_prompt_local_time="25:00")

    def test_the_symptom_report_cap_is_bounded(self):
        # A cap protects the evaluation from an unbounded request body. Zero
        # would break the endpoint entirely; a huge value would not protect it.
        with pytest.raises(ValueError):
            _settings(checkin_max_symptom_reports=0)
        with pytest.raises(ValueError):
            _settings(checkin_max_symptom_reports=1000)

    def test_the_prompt_time_property_reads_as_a_time(self):
        assert _settings(checkin_prompt_local_time="07:30").checkin_prompt_time.hour == 7

    def test_the_prompt_time_defaults_to_a_sane_morning(self):
        assert _settings().checkin_prompt_time.strftime("%H:%M") == "09:00"


class TestPromptReminderRendering:
    """
    The daily prompt is a Phase 5 reminder, so its body is rendered by the
    existing reminder service. These tests only care that the Phase 6 prompt
    does not read like a clinical instruction.
    """

    def test_the_prompt_names_the_checkin_and_asks_for_nothing_clinical(
        self, phase6_services, make_patient
    ):
        from app.schemas.reminder import CheckInReminderCreate

        patient = make_patient()
        reminder = phase6_services["reminders"].create_checkin_reminder(
            patient.id,
            CheckInReminderCreate(local_time="09:00", timezone="UTC"),
        )
        body = phase6_services["notifications"]._render_body(reminder)
        assert body
        assert "check-in" in body.lower()
        # A prompt must not smuggle in advice, urgency, or a diagnosis.
        for forbidden in ("emergency", "urgent", "diagnos", "911"):
            assert forbidden not in body.lower()

    def test_a_paused_prompt_is_never_dispatched(
        self, phase6_services, make_patient
    ):
        from app.schemas.reminder import CheckInReminderCreate

        patient = make_patient()
        reminder = phase6_services["reminders"].create_checkin_reminder(
            patient.id,
            CheckInReminderCreate(local_time="09:00", timezone="UTC"),
        )
        reminder.status = ReminderStatus.paused
        phase6_services["db"].commit()

        result = phase6_services["scheduler"].dispatch_due()
        assert result.materialized == 0

    def test_a_due_prompt_materialises_a_checkin_prompt_notification(
        self, phase6_services, make_patient
    ):
        from app.models.notification import Notification
        from app.schemas.reminder import CheckInReminderCreate

        patient = make_patient()
        reminder = phase6_services["reminders"].create_checkin_reminder(
            patient.id,
            CheckInReminderCreate(local_time="09:00", timezone="UTC"),
        )
        # Stamp the occurrence and dispatch against that exact instant. The due
        # window is inclusive of `now`, so stamping `utcnow()` and dispatching
        # a few microseconds later would scan nothing and the test would pass
        # for the wrong reason.
        when = _stamp_due(phase6_services["db"], reminder.id)

        result = phase6_services["scheduler"].dispatch_due(now=when)
        assert result.materialized == 1

        notice = phase6_services["db"].query(Notification).one()
        assert notice.notification_type.value == "checkin_prompt"
        assert notice.reminder_id == reminder.id
        assert notice.medication_id is None


def _stamp_due(db, reminder_id):
    """Force a reminder's next occurrence to now, and return that instant."""
    from datetime import timedelta

    from app.core.timezones import utcnow

    when = utcnow() + timedelta(seconds=5)
    db.execute(
        __import__("sqlalchemy").text(
            "UPDATE reminders SET next_occurrence_at = :when WHERE id = :id"
        ),
        {"when": when, "id": str(reminder_id)},
    )
    db.commit()
    return when


class TestCeleryRecoveryTask:
    def test_the_task_is_registered_and_named(self):
        """
        The beat schedule names a task string.

        A typo between `celery_app.py` and `tasks.py` is silent at startup and
        only shows up as a log line about an unregistered task, so the two
        names are compared here instead.
        """
        from app.workers import celery_app

        scheduled = [entry["task"] for entry in _escalation_schedule_entries()]
        assert scheduled
        for name in scheduled:
            assert name in celery_app.tasks, (
                f"beat schedule names {name!r} but no such task is registered"
            )

    def test_the_recovery_task_runs_every_minute(self):
        # Every minute: an escalation that could not be materialised inline is
        # a caregiver who has not been told, and that gap should be short.  The
        # cadence matches the other two per-minute Phase 5 tasks, expressed as
        # a crontab rather than a raw interval for the same reason.
        entries = _escalation_schedule_entries()
        assert len(entries) == 1
        schedule = entries[0]["schedule"]
        # Celery expands `minute="*"` into all 60 minute values, so assert the
        # expansion rather than the source expression.
        assert set(schedule.minute) == set(range(60))

    def test_the_task_only_materialises_and_never_sends(self):
        """
        The recovery task builds notifications; delivery stays with Phase 5.

        A second send path would be a second retry policy and a second place
        for a duplicate caregiver alert to be born.  `co_names` is used rather
        than the source text because the docstring legitimately NAMES the
        delivery task in order to explain that it is not being called here.
        """
        from app.workers import tasks

        task = tasks.notify_pending_escalations
        # A bound Celery task exposes the function as `.run`.
        referenced = set(getattr(task.run, "__code__", task.run).co_names)
        assert "retry_due" not in referenced
        assert "deliver_pending" not in referenced

    def test_running_recovery_twice_makes_one_notice_per_escalation(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        from app.models.notification import Notification

        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        _create(phase6_services, patient, symptom)

        escalations = phase6_services["escalations"].list_for_patient(patient.id)
        assert escalations

        # The inline request already materialised them, so recovery has nothing
        # to do - and doing nothing twice must be indistinguishable from doing
        # nothing once. This is the property that lets the task run forever.
        first = phase6_services["escalations"].notify_pending()
        second = phase6_services["escalations"].notify_pending()

        assert first["materialized"] == 0
        assert second["materialized"] == 0
        assert (
            phase6_services["db"].query(Notification).count()
            == len(escalations)
        )

    def test_delivery_is_idempotent_across_repeated_passes(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        from app.models.notification import Notification

        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        _create(phase6_services, patient, symptom)

        provider = phase6_services["provider"]
        scheduler = phase6_services["scheduler"]
        scheduler.dispatch_due()
        scheduler.deliver_pending()
        first = provider.send_count
        assert first > 0

        scheduler.deliver_pending()
        assert provider.send_count == first

        assert all(
            n.status == NotificationStatus.sent
            for n in phase6_services["db"].query(Notification).all()
        )


def _escalation_schedule_entries() -> list:
    """
    The beat entries for the Phase 6 task, read from the real schedule.

    `app.workers.__init__` re-exports the Celery instance under the name
    `celery_app`, so the imported name IS the app - there is no second
    `.celery_app` attribute to reach through.
    """
    from app.workers import celery_app

    assert isinstance(celery_app.conf.beat_schedule, dict)
    return [
        entry
        for entry in celery_app.conf.beat_schedule.values()
        if "escalation" in entry["task"]
    ]


def _create(phase6_services, patient, symptom) -> None:
    from app.schemas.checkin import DailyCheckInCreate

    phase6_services["checkins"].submit(
        patient,
        DailyCheckInCreate(
            general_wellbeing="unwell",
            condition_change="worse",
            warning_symptoms=[
                {"symptom_id": symptom.id, "change": "worse"}
            ],
        ),
    )
