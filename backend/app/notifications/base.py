"""
CareLoop AI — Notification Provider Abstraction (Phase 5)

The contract that lets a messaging vendor be added without touching any
scheduling, retry, or persistence logic.

A provider has exactly one job: take a rendered message and hand it to a
transport, then report what happened.  Everything else - when to fire,
idempotency, backoff, history, timezone maths - lives above this interface and
is provider-agnostic.  Adding WhatsApp means writing one class here; adding the
next vendor after that means the same, and no service changes.

The error split is the other half of the contract.  A provider must say
explicitly whether a failure is worth retrying, because the two cases have
opposite responses:

  * `NotificationTransientError` - retry with backoff, then give up.
  * `NotificationPermanentError` - do not retry; a rejected address or revoked
    credential will fail identically forever.

Providers raise; the scheduler decides.  That is what keeps the retry policy in
one place instead of smeared across vendor SDKs.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field

from app.core.exceptions import (
    NotificationPermanentError,
    NotificationTransientError,
)


@dataclass(frozen=True)
class DeliveryReceipt:
    """
    Proof that a provider accepted a message.

    `message_id` is the provider's own identifier where one exists, stored for
    support and for de-duplication on the provider's side.  It is optional
    because not every transport returns one.
    """

    provider_message_id: str | None = None
    # Free-form, provider-specific status token.  Never raw payload: it can
    # echo the recipient or the body, and it is written to a log line.
    status: str = "accepted"
    # Raw diagnostic detail, safe for the database but never for a client or a
    # log.  Redacted by the caller before it is persisted.
    detail: str | None = field(default=None, repr=False)


class NotificationProvider(abc.ABC):
    """
    Base class for every delivery transport.

    Implementations must be safe to construct without credentials being
    present, and must raise `NotificationProviderNotConfiguredError` from
    `send` rather than at construction time - a misconfigured deployment
    should fail the delivery, not the whole worker.
    """

    # Stable identifier, stored on each notification row.  Changing it would
    # break the meaning of existing history, so treat it as permanent.
    channel: str

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Whether this provider has everything it needs to send."""

    @abc.abstractmethod
    def send(self, *, recipient: str, body: str) -> DeliveryReceipt:
        """
        Deliver one message.

        Args:
            recipient: Destination address.  Sourced from the patient record;
                never accepted from an API request body.
            body: The rendered message.

        Returns:
            A `DeliveryReceipt` on success.

        Raises:
            NotificationTransientError: worth retrying (timeout, 5xx, 429).
            NotificationPermanentError: retrying cannot help.
            NotificationProviderNotConfiguredError: credentials absent.
        """

    def ensure_configured(self) -> None:
        """
        Raise unless the provider can send.

        Named separately from `is_configured` so the check is one call site in
        the delivery path rather than an `if` the caller might forget.
        """
        if not self.is_configured():
            from app.core.exceptions import (  # local import: avoids a cycle
                NotificationProviderNotConfiguredError,
            )

            raise NotificationProviderNotConfiguredError(
                f"The '{self.channel}' notification provider is selected but "
                "has no credentials configured."
            )

    def describe(self) -> str:
        """A short, secret-free description for the readiness endpoint."""
        return f"{self.channel} (configured={self.is_configured()})"


__all__ = [
    "DeliveryReceipt",
    "NotificationProvider",
    "NotificationTransientError",
    "NotificationPermanentError",
]
