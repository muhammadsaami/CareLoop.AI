"""
CareLoop AI — Console Notification Provider (Phase 5)

The default, and the only provider that is guaranteed safe in development and
in CI: it sends nothing anywhere.

It exists so the entire scheduling pipeline - dispatch, materialise, deliver,
record, retry - can be exercised end to end without a messaging vendor, an
account, a phone number, or a network call.  Tests assert that a dispatched
reminder produces a `sent` notification and that nothing was transmitted.

It is also a real, if humble, transport for local development: the delivery is
recorded to the application log so a developer can see that a reminder fired.

PHI DISCIPLINE
The rendered body is clinical content ("Take 1 tablet of Metformin at 08:00").
It is never logged, at any level, and neither is the recipient - only the
character count is recorded, which is enough to debug an empty message without
writing patient-facing clinical text into a log file.  The INFO line carries
the channel and the outcome, which is what an operator actually needs.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import Settings
from app.notifications.base import DeliveryReceipt, NotificationProvider

logger = logging.getLogger(__name__)


class ConsoleNotificationProvider(NotificationProvider):
    """Records a delivery attempt to the log instead of transmitting it."""

    channel = "console"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """
        Accepts and ignores `settings` so every provider in the registry shares
        one constructor signature.  The console transport has nothing to
        configure, but uniformity is what lets the factory stay a plain dict.
        """
        self._settings = settings

    def is_configured(self) -> bool:
        # Always ready: there is nothing to configure and nothing to send.
        return True

    def send(self, *, recipient: str, body: str) -> DeliveryReceipt:
        """
        Record the attempt. Never fails and never transmits.

        `recipient` is intentionally not logged.  The body is logged at DEBUG
        only, because DEBUG is off in production configuration and a body is
        still PHI-adjacent wherever it lands.
        """
        # CareLoop's logging context binds patient/thread identity to the record
        # when configured, so ids propagate without being passed in here.
        logger.info(
            "notification_delivered channel=%s transport=console",
            self.channel,
        )
        logger.debug(
            "notification_body_size channel=%s body_chars=%d",
            self.channel,
            len(body),
        )
        return DeliveryReceipt(
            provider_message_id=None,
            status="logged",
            detail="console provider: no message transmitted",
        )
