"""
CareLoop AI — Notification Provider Tests (Phase 5)

The provider layer is the only part of Phase 5 that would touch a third party,
so it carries the most risk per line.  Three things are proved here:

1. Nothing leaves the process unless a provider is explicitly configured.  A
   missing credential must be a clean, typed failure - never a silent send, and
   never a network call.
2. Errors are classified into transient and permanent correctly, because that
   split decides whether a patient gets a second attempt.
3. No secret, recipient number, or message body ever reaches a log line or an
   error string.
"""
import json
import logging

import pytest

from app.core.exceptions import (
    NotificationProviderError,
    NotificationProviderNotConfiguredError,
    NotificationPermanentError,
    NotificationTransientError,
)
from app.core.config import get_settings
from app.notifications.base import DeliveryReceipt
from app.notifications.console import ConsoleNotificationProvider
from app.notifications.factory import (
    build_provider,
    require_configured_provider,
)
from app.notifications.whatsapp import WhatsAppNotificationProvider


class _Response:
    """Minimal stand-in for an httpx response."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class TestFactory:
    def test_default_provider_is_console(self):
        settings = get_settings().model_copy(
            update={"notification_provider": "console"}
        )
        assert isinstance(
            build_provider(settings), ConsoleNotificationProvider
        )

    def test_whatsapp_is_selected_by_name(self):
        settings = get_settings().model_copy(
            update={
                "notification_provider": "whatsapp",
                "whatsapp_access_token": "t",
                "whatsapp_phone_number_id": "1",
            }
        )
        assert isinstance(build_provider(settings), WhatsAppNotificationProvider)

    def test_unknown_provider_name_is_rejected(self):
        from app.core.exceptions import ConfigurationError

        settings = get_settings().model_copy(
            update={"notification_provider": "carrier-pigeon"}
        )
        with pytest.raises(ConfigurationError):
            build_provider(settings)

    def test_blank_provider_name_is_rejected_not_defaulted(self):
        """
        An empty name is a misconfiguration, not a request for the default.  A
        silent fallback would mean an operator who cleared the setting gets
        console delivery without any signal that nothing is being sent.
        """
        from app.core.exceptions import ConfigurationError

        settings = get_settings().model_copy(update={"notification_provider": ""})
        with pytest.raises(ConfigurationError):
            build_provider(settings)

    def test_require_configured_reports_deployment_problem(self):
        """
        A selected-but-unconfigured provider is a deployment fault, and must not
        be reported as a per-message delivery failure.
        """
        from app.core.exceptions import NotificationProviderNotConfiguredError

        settings = get_settings().model_copy(
            update={
                "notification_provider": "whatsapp",
                "whatsapp_access_token": "",
                "whatsapp_phone_number_id": "",
            }
        )
        with pytest.raises(NotificationProviderNotConfiguredError):
            require_configured_provider(settings)

    def test_require_configured_passes_a_working_console(self):
        settings = get_settings().model_copy(
            update={"notification_provider": "console"}
        )
        assert isinstance(
            require_configured_provider(settings), ConsoleNotificationProvider
        )


class TestConsoleProvider:
    def test_reports_itself_configured(self):
        assert ConsoleNotificationProvider().is_configured() is True

    def test_returns_a_successful_delivery(self):
        """
        Success is signalled by not raising, so "no exception" is the assertion.
        The console transport has no vendor to return an id, so the receipt's
        `provider_message_id` is legitimately None.
        """
        provider = ConsoleNotificationProvider()
        receipt = provider.send(
            recipient="+15550100", body="Time for your medication"
        )
        assert isinstance(receipt, DeliveryReceipt)
        assert receipt.provider_message_id is None
        assert receipt.status

    def test_never_dials_anything(self, monkeypatch):
        """
        A regression guard: the console provider must not gain a network call.
        """
        import httpx

        def _explode(*args, **kwargs):  # pragma: no cover
            raise AssertionError("console provider must not make requests")

        monkeypatch.setattr(httpx, "post", _explode)
        monkeypatch.setattr(httpx.Client, "post", _explode, raising=False)
        ConsoleNotificationProvider().send(
            recipient="+15550100", body="hello"
        )


class TestWhatsAppConfiguration:
    def test_unconfigured_without_credentials(self):
        provider = WhatsAppNotificationProvider(
            settings=get_settings().model_copy(
                update={"whatsapp_access_token": "", "whatsapp_phone_number_id": ""}
            )
        )
        assert provider.is_configured() is False

    def test_unconfigured_provider_raises_typed_error_without_sending(
        self, monkeypatch
    ):
        import httpx

        def _explode(*args, **kwargs):  # pragma: no cover
            raise AssertionError("must not call the API when unconfigured")

        monkeypatch.setattr(httpx, "post", _explode)
        provider = WhatsAppNotificationProvider(
            settings=get_settings().model_copy(
                update={"whatsapp_access_token": "", "whatsapp_phone_number_id": ""}
            )
        )

        with pytest.raises(NotificationProviderNotConfiguredError):
            provider.send(recipient="+15550100", body="hi")

    def test_not_configured_is_distinguishable_from_a_send_failure(self):
        """
        They mean different things operationally - one is a deployment problem,
        the other a per-message problem - so they must not share a type.
        """
        assert not issubclass(
            NotificationProviderNotConfiguredError, NotificationTransientError
        )
        assert not issubclass(
            NotificationProviderNotConfiguredError, NotificationPermanentError
        )


class _FakeClient:
    """
    Stands in for `httpx.Client`.

    The provider takes its client through the constructor, so these tests need
    no real socket and no monkeypatching of library internals.
    """

    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def close(self):
        return None


class TestWhatsAppDelivery:
    def _provider(self, response, **overrides):
        settings = get_settings().model_copy(
            update={
                "whatsapp_access_token": "test-token",
                "whatsapp_phone_number_id": "123456",
                **overrides,
            }
        )
        client = _FakeClient(response)
        return WhatsAppNotificationProvider(settings=settings, client=client), client

    def test_successful_send_returns_provider_id(self):
        provider, client = self._provider(
            _Response(200, {"messages": [{"id": "wamid.ABC123"}]}),
        )
        receipt = provider.send(
            recipient="+15550100", body="Time for your medication"
        )
        # The vendor's own id, kept for support and provider-side dedup.
        assert receipt.provider_message_id == "wamid.ABC123"
        assert client.calls, "expected the provider to call the API"

    def test_rate_limit_is_transient(self):
        """
        A 429 must be retried with backoff.  Treating it as permanent would
        silently drop a reminder for a patient whose provider is merely busy.
        """
        provider, _ = self._provider(
            _Response(429, {"error": "rate limited"})
        )
        with pytest.raises(NotificationTransientError):
            provider.send(recipient="+15550100", body="hi")

    def test_server_error_is_transient(self):
        provider, _ = self._provider(
            _Response(500, {"error": "server error"})
        )
        with pytest.raises(NotificationTransientError):
            provider.send(recipient="+15550100", body="hi")

    def test_invalid_recipient_is_permanent(self):
        """
        A 4xx about the number itself will never succeed on retry.  Retrying it
        burns attempts and delays the rest of the batch.
        """
        provider, _ = self._provider(
            _Response(400, {"error": "invalid phone number"})
        )
        with pytest.raises(NotificationPermanentError):
            provider.send(recipient="+1555", body="hi")

    def test_malformed_success_body_is_an_error(self):
        """
        A 200 whose body we cannot read is not a delivery we can prove happened.
        Treating it as success would silently mark the message sent.
        """
        provider, _ = self._provider(_Response(200, {"unexpected": 1}))
        with pytest.raises(NotificationProviderError):
            provider.send(recipient="+15550100", body="hi")

    def test_transport_timeout_is_transient(self):
        """A socket timeout says nothing about whether a retry would work."""
        import httpx

        provider, _ = self._provider(httpx.TimeoutException("timed out"))
        with pytest.raises(NotificationTransientError):
            provider.send(recipient="+15550100", body="hi")


class TestSecretHandling:
    def test_error_messages_never_carry_the_token_or_body(self, caplog):
        """
        A vendor error message is vendor-controlled and frequently echoes back
        exactly what we must not store or log - here the token and the number.
        It has to be reduced before it reaches the exception or the log.
        """
        settings = get_settings().model_copy(
            update={"whatsapp_access_token": "super-secret-token", "whatsapp_phone_number_id": "1"}
        )
        client = _FakeClient(
            _Response(
                400,
                {
                    "error": {
                        "message": "token super-secret-token rejected for +15550100"
                    }
                },
            )
        )
        provider = WhatsAppNotificationProvider(settings=settings, client=client)

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(NotificationProviderError) as excinfo:
                provider.send(
                    recipient="+15550100",
                    body="Metformin 500mg with breakfast",
                )

        rendered = str(excinfo.value)
        assert "super-secret-token" not in rendered
        assert "+15550100" not in rendered
        assert "Metformin" not in rendered

        logged = caplog.text + "\n".join(
            r.getMessage() for r in caplog.records
        )
        for secret in ("super-secret-token", "+15550100", "Metformin"):
            assert secret not in logged

    def test_receipt_detail_is_redacted_not_the_raw_vendor_payload(self, caplog):
        """
        `DeliveryReceipt.detail` is kept for the database, so a success response
        that echoes the recipient must not be stored verbatim.
        """
        settings = get_settings().model_copy(
            update={"whatsapp_access_token": "tok", "whatsapp_phone_number_id": "1"}
        )
        client = _FakeClient(
            _Response(
                200,
                {
                    "messages": [{"id": "wamid.1"}],
                    "contacts": [{"input": "+15550100", "wa_id": "+15550100"}],
                },
            )
        )
        provider = WhatsAppNotificationProvider(settings=settings, client=client)

        with caplog.at_level(logging.DEBUG):
            receipt = provider.send(
                recipient="+15550100", body="Metformin 500mg"
            )

        if receipt.detail:
            assert "+15550100" not in receipt.detail
        logged = caplog.text
        assert "Metformin" not in logged

    def test_request_headers_are_not_logged(self, caplog):
        settings = get_settings().model_copy(
            update={"whatsapp_access_token": "bearer-secret-value", "whatsapp_phone_number_id": "1"}
        )
        client = _FakeClient(_Response(200, {"messages": [{"id": "x"}]}))
        provider = WhatsAppNotificationProvider(settings=settings, client=client)

        with caplog.at_level(logging.DEBUG):
            provider.send(recipient="+15550100", body="hi")

        assert "bearer-secret-value" not in caplog.text


class TestSafeExceptionLogging:
    """
    Guards `log_exception_without_phi`, which exists precisely because
    `logger.exception` writes the exception message.
    """

    def test_logs_type_but_not_message_or_traceback(self, caplog):
        from app.core.logging import log_exception_without_phi

        logger = logging.getLogger("test.safe")
        boom = RuntimeError("patient John Smith 555-0100 amoxicillin 500mg")

        with caplog.at_level(logging.ERROR, logger="test.safe"):
            try:
                raise boom
            except RuntimeError as exc:
                log_exception_without_phi(
                    logger, "something_failed", exc, reminder_id="abc-123"
                )

        message = caplog.records[0].getMessage()
        assert "RuntimeError" in message
        assert "reminder_id=abc-123" in message
        for secret in ("John Smith", "555-0100", "amoxicillin", "500mg"):
            assert secret not in message
        # No traceback attached.
        assert caplog.records[0].exc_info is None

    def test_still_records_the_log_level_and_extra_fields(self, caplog):
        from app.core.logging import log_exception_without_phi

        logger = logging.getLogger("test.safe2")
        with caplog.at_level(logging.WARNING, logger="test.safe2"):
            try:
                raise ValueError("nope")
            except ValueError as exc:
                log_exception_without_phi(
                    logger, "warned", exc, level=logging.WARNING, count=3
                )

        record = caplog.records[0]
        assert record.levelno == logging.WARNING
        assert "count=3" in record.getMessage()
