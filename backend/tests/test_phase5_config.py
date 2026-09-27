"""
CareLoop AI — Phase 5 Configuration Tests

Configuration is where a safety property is either real or assumed.  A reminder
engine that starts with no broker, or a retry budget that expires before its
own backoff grows, does not fail loudly at review time - it fails at 3am with a
patient missing a dose, so the validators are tested here rather than trusted.
"""
import pytest
from pydantic import ValidationError

from app.core.config import Settings


class TestRedisUrl:
    def test_blank_redis_url_falls_back_to_the_default(self):
        """
        An env file carrying `REDIS_URL=` with no value is easy to reach by
        copying a template.  Left alone it would silently beat the field default
        and start the worker with an empty broker.
        """
        assert Settings(redis_url="").redis_url == "redis://localhost:6379/0"
        assert Settings(redis_url="   ").redis_url == "redis://localhost:6379/0"

    def test_explicit_redis_url_is_respected(self):
        assert (
            Settings(redis_url="redis://cache-host:6380/2").redis_url
            == "redis://cache-host:6380/2"
        )


class TestProviderValidation:
    def test_console_is_the_default(self):
        """
        A deployment that has not chosen a transport must not be able to send a
        message to a patient by accident.
        """
        assert Settings().notification_provider == "console"

    def test_whatsapp_is_accepted_but_opt_in(self):
        assert Settings(notification_provider="whatsapp").notification_provider == "whatsapp"

    def test_unknown_provider_is_rejected_at_startup(self):
        """
        Rejected while loading settings rather than at the first send, so a typo
        is a boot failure the operator sees, not a silent fall back to console.
        """
        with pytest.raises(ValidationError):
            Settings(notification_provider="carrier-pigeon")

    def test_blank_provider_is_rejected(self):
        with pytest.raises(ValidationError):
            Settings(notification_provider="")


class TestRetryBudget:
    def test_max_attempts_must_be_positive(self):
        with pytest.raises(ValidationError):
            Settings(notification_max_attempts=0)

    def test_base_backoff_must_be_positive(self):
        with pytest.raises(ValidationError):
            Settings(notification_retry_base_seconds=0)

    def test_max_backoff_must_not_undercut_the_base(self):
        """
        A MAX below BASE means the first backoff is already clamped, so every
        retry after it fires at the same instant - a tight retry loop against a
        struggling provider.
        """
        with pytest.raises(ValidationError):
            Settings(
                notification_retry_base_seconds=600,
                notification_retry_max_seconds=60,
            )

    def test_equal_base_and_max_is_allowed(self):
        """A flat, constant backoff is a legitimate configuration."""
        settings = Settings(
            notification_retry_base_seconds=60,
            notification_retry_max_seconds=60,
        )
        assert settings.notification_retry_max_seconds == 60

    def test_provider_timeout_must_be_positive(self):
        with pytest.raises(ValidationError):
            Settings(notification_timeout_seconds=0)


class TestSchedulerSettings:
    def test_lookahead_may_be_zero(self):
        """
        Zero is meaningful: it materialises only what is already due.  A
        validator that rejected it would make "no look-ahead" inexpressible.
        """
        assert Settings(scheduler_lookahead_seconds=0).scheduler_lookahead_seconds == 0

    def test_negative_lookahead_is_rejected(self):
        with pytest.raises(ValidationError):
            Settings(scheduler_lookahead_seconds=-1)

    def test_batch_size_must_be_positive(self):
        """
        Zero would make every tick a no-op that looks like a healthy, idle
        scheduler while sending nothing at all.
        """
        with pytest.raises(ValidationError):
            Settings(scheduler_batch_size=0)

    def test_default_lookahead_exceeds_the_beat_interval(self):
        """
        The dispatch tick runs every 60s.  If the look-ahead were shorter than
        one tick, an occurrence landing just after a scan would not be
        materialised until the next one - and a single missed tick would
        silently skip it entirely, which for a medication means a missed dose.
        """
        settings = Settings()
        assert settings.scheduler_lookahead_seconds >= 60


class TestQueueConfiguration:
    def test_empty_queue_is_rejected(self):
        """
        An empty queue name would publish to the default "celery" queue, where
        a Phase 5 worker could pick up an unrelated task from another app.
        """
        with pytest.raises(ValidationError):
            Settings(celery_queue="   ")

    def test_default_queue_is_namespaced(self):
        assert Settings().celery_queue.startswith("careloop")


class TestPatientTimezoneValidation:
    def test_valid_iana_zone_is_accepted(self):
        from app.schemas.patient import PatientCreate

        patient = PatientCreate(
            name="Test", contact_number="+15550100", timezone="Asia/Kolkata"
        )
        assert patient.timezone == "Asia/Kolkata"

    def test_invalid_zone_is_rejected_on_the_patient_record(self):
        """
        Validated where the mistake is made.  Storing a bad zone would fail
        later, at reminder-creation time, with no link back to the edit that
        caused it.
        """
        from app.schemas.patient import PatientCreate

        with pytest.raises(ValidationError):
            PatientCreate(
                name="Test", contact_number="+15550100", timezone="Mars/Olympus"
            )

    def test_timezone_defaults_to_utc(self):
        from app.schemas.patient import PatientCreate

        assert PatientCreate(name="T", contact_number="+1").timezone == "UTC"
