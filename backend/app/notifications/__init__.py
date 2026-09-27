"""
CareLoop AI — Notification Providers (Phase 5)

Transport-agnostic delivery. Import the abstractions here; import concrete
providers from their own modules so that adding a vendor does not widen this
package's surface.
"""
from app.notifications.base import (
    DeliveryReceipt,
    NotificationProvider,
    NotificationTransientError,
    NotificationPermanentError,
)
from app.notifications.console import ConsoleNotificationProvider
from app.notifications.factory import (
    build_provider,
    require_configured_provider,
)
from app.notifications.whatsapp import WhatsAppNotificationProvider

__all__ = [
    "DeliveryReceipt",
    "NotificationProvider",
    "NotificationTransientError",
    "NotificationPermanentError",
    "ConsoleNotificationProvider",
    "WhatsAppNotificationProvider",
    "build_provider",
    "require_configured_provider",
]
