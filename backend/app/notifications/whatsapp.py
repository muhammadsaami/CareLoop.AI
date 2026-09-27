"""
CareLoop AI — WhatsApp Notification Provider (Phase 5)

A real, opt-in transport that exists to prove the abstraction: this class is the
entire cost of adding a messaging vendor.  No service, repository, migration,
or scheduler code is aware that WhatsApp exists.

It is selected only when NOTIFICATION_PROVIDER=whatsapp.  With no credentials
it is *constructed successfully* and fails at send time with a 503-shaped
misconfiguration error, so a half-configured deployment degrades one delivery
rather than preventing the worker from booting.  This also means the test suite
never needs a WhatsApp account, a token, or a phone number: the default
provider is the console one, and the tests for this class inject a fake HTTP
transport instead.

Secret handling
The access token is read from settings, never hardcoded, and never logged.

No vendor response body is ever placed in an error message.  That is a stronger
rule than the Phase 2 `redact_secrets` helper provides, and deliberately so:
redaction removes credentials, but a WhatsApp 4xx body echoes the *recipient's
phone number*, which is patient data.  Rather than teach a secret redactor to
guess at personal data, the vendor payload is simply not propagated - the
message carries a status code and nothing else.  Anything that needs the real
body reads it from the vendor's own dashboard.

Failure classification maps HTTP status to the retry policy:
  429, 5xx, transport errors, timeouts  -> transient (retry with backoff)
  4xx other than 429                     -> permanent (do not retry)
  2xx with no readable message id        -> transient (delivery unconfirmed)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    NotificationPermanentError,
    NotificationTransientError,
)
from app.notifications.base import DeliveryReceipt, NotificationProvider

logger = logging.getLogger(__name__)

# Meta's Cloud API base.  Version-pinned: the Graph API is versioned per
# release, and a floating /latest would change response shape under us.
_GRAPH_VERSION = "v21.0"
_GRAPH_BASE = f"https://graph.facebook.com/{_GRAPH_VERSION}"


class WhatsAppNotificationProvider(NotificationProvider):
    """Sends via the WhatsApp Cloud API. Opt-in; off by default."""

    channel = "whatsapp"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        client: Optional[httpx.Client] = None,
    ) -> None:
        """
        Args:
            settings: Injected in tests to avoid touching the environment.
            client: Injected in tests to avoid a network call.  Production
                leaves this unset and a client is built lazily.
        """
        self._settings = settings or get_settings()
        self._client = client

    # ── Configuration ──────────────────────────────────────────────────────
    def is_configured(self) -> bool:
        return bool(
            self._settings.whatsapp_access_token
            and self._settings.whatsapp_phone_number_id
        )

    # ── Delivery ───────────────────────────────────────────────────────────
    def send(self, *, recipient: str, body: str) -> DeliveryReceipt:
        """
        POST one text message to the Cloud API.

        Raises `NotificationTransientError` or `NotificationPermanentError`;
        the scheduler above decides what to do about either.
        """
        # Raises NotificationProviderNotConfiguredError when credentials are
        # absent, before any network call is attempted.
        self.ensure_configured()

        if not recipient or not recipient.strip():
            raise NotificationPermanentError(
                "The notification has no destination address."
            )

        payload = {
            "messaging_product": "whatsapp",
            "to": recipient.strip(),
            "type": "text",
            "text": {"body": body},
        }
        url = f"{_GRAPH_BASE}/{self._settings.whatsapp_phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        }

        try:
            response = self._get_client().post(
                url, json=payload, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise NotificationTransientError(
                "The WhatsApp API did not respond in time."
            ) from exc
        except httpx.HTTPError as exc:
            # Connection reset, DNS failure, pool exhaustion: all plausibly
            # transient.
            raise NotificationTransientError(
                "The WhatsApp API could not be reached."
            ) from exc

        if response.status_code >= 200 and response.status_code < 300:
            return self._parse_success(response)

        raise self._translate_error(response)

    # ── Internals ──────────────────────────────────────────────────────────
    def _get_client(self) -> httpx.Client:
        """Build the HTTP client on first use, mirroring the LLM provider."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._settings.notification_timeout_seconds
            )
        return self._client

    def _parse_success(self, response: httpx.Response) -> DeliveryReceipt:
        """
        Extract the provider's message id from a 2xx response.

        A 2xx whose body cannot be read, or that carries no message id, is
        treated as a transient failure rather than a success.  We cannot prove
        the message was accepted, and recording an unproven delivery as `sent`
        is the one outcome that silently loses a reminder: the retry pass would
        skip it forever.  Retrying is safe instead - the idempotency key on the
        row stops our own layer from double-sending, and the provider's message
        id is what would expose a duplicate if one occurred.
        """
        try:
            payload = response.json()
        except ValueError as exc:
            raise NotificationTransientError(
                "The WhatsApp API returned a response that could not be read."
            ) from exc

        messages = payload.get("messages") if isinstance(payload, dict) else None
        message_id = None
        if messages and isinstance(messages[0], dict):
            message_id = messages[0].get("id")

        if not message_id:
            raise NotificationTransientError(
                "The WhatsApp API returned a success status without a message "
                "id, so the delivery could not be confirmed."
            )

        return DeliveryReceipt(
            provider_message_id=message_id,
            status="accepted",
            detail="whatsapp message accepted",
        )

    def _translate_error(self, response: httpx.Response) -> Exception:
        """
        Map a non-2xx response to transient or permanent.

        The message names the status code and nothing else.  A vendor error body
        is not merely noisy, it is a disclosure: WhatsApp echoes the recipient
        number back in most of its 4xx bodies, and that text would land in
        `Notification.last_error`, in an API response, and in any log line that
        renders the exception.  Redaction does not rescue it - the redaction
        helpers cover credentials, not a patient's contact details, and a
        client-safe message has no business carrying a third party's payload in
        the first place.  The vendor's own dashboard keeps the detail for anyone
        who genuinely needs to debug a delivery.
        """
        status = response.status_code

        if status == 429 or status >= 500:
            return NotificationTransientError(
                f"The WhatsApp API returned a transient failure "
                f"(status {status})."
            )

        return NotificationPermanentError(
            f"The WhatsApp API rejected the message (status {status})."
        )
