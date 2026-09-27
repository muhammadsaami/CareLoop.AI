"""
CareLoop AI — Notification Provider Factory (Phase 5)

Resolves the configured provider name to a provider instance.

The registry is a plain dict, so adding a vendor is a one-line change plus its
class - and, importantly, the settings validator that accepts its name.  Nothing
else in the system needs to know it exists.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ConfigurationError,
    NotificationProviderNotConfiguredError,
)
from app.notifications.base import NotificationProvider
from app.notifications.console import ConsoleNotificationProvider
from app.notifications.whatsapp import WhatsAppNotificationProvider

# Name -> constructor.  Keys must match the values accepted by the
# NOTIFICATION_PROVIDER validator in Settings.
_REGISTRY: Dict[str, Callable[[Settings], NotificationProvider]] = {
    "console": ConsoleNotificationProvider,
    "whatsapp": WhatsAppNotificationProvider,
}


def build_provider(
    settings: Optional[Settings] = None,
    *,
    name: Optional[str] = None,
) -> NotificationProvider:
    """
    Instantiate the selected provider.

    Args:
        settings: Injected in tests.
        name: Override the configured name, for a per-request or per-task
            channel.  Falls back to NOTIFICATION_PROVIDER.

    Raises:
        ConfigurationError: the name is not registered.  Unreachable through
            normal configuration, because Settings validates the name first;
            kept as a guard for the programmatic `name=` path.
    """
    resolved_settings = settings or get_settings()
    provider_name = (name or resolved_settings.notification_provider or "").strip().lower()

    factory = _REGISTRY.get(provider_name)
    if factory is None:
        raise ConfigurationError(
            f"Unknown notification provider: {provider_name!r}. "
            f"Available: {', '.join(sorted(_REGISTRY))}."
        )

    return factory(resolved_settings)


def require_configured_provider(
    settings: Optional[Settings] = None,
    *,
    name: Optional[str] = None,
) -> NotificationProvider:
    """
    Build a provider and confirm it can actually send.

    Used by the readiness check and by the delivery path.  Raises
    `NotificationProviderNotConfiguredError` when the selected provider is
    missing credentials, so the failure is reported as a deployment problem
    rather than as a failed delivery.
    """
    provider = build_provider(settings, name=name)
    if not provider.is_configured():
        raise NotificationProviderNotConfiguredError(
            f"The '{provider.channel}' notification provider is selected but "
            "its credentials are not configured."
        )
    return provider


__all__ = [
    "build_provider",
    "require_configured_provider",
    "ConsoleNotificationProvider",
    "WhatsAppNotificationProvider",
]
