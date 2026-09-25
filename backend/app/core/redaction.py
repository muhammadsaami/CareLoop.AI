"""
CareLoop AI — Secret Redaction (Phase 2)

The domain-exception contract says ``message`` is client-safe and
``internal_detail`` is server-only.  Providers honour that today, but a
future provider that interpolates a vendor payload into a message would leak
an API key straight to the client.

This module makes the guarantee structural rather than conventional: every
outgoing error detail is passed through here first.

Two layers, because either alone is insufficient:
  1. Exact redaction of the secrets this process actually has configured,
     which catches any key regardless of its format.
  2. Pattern redaction for well-known provider key prefixes, which catches
     keys that are not currently configured (e.g. echoed back by a vendor).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Well-known credential shapes.  Patterns are deliberately anchored to a
# recognisable prefix so ordinary clinical prose is never altered.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgsk_[A-Za-z0-9_\-]{16,}"),          # Groq
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),           # Google
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),           # OpenAI-style
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),              # GitHub
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),     # Slack
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"), # Authorization
)

REDACTED = "[redacted]"


def _configured_secrets(settings: Optional[object] = None) -> list[str]:
    """
    Collect secret values from settings, if any are configured.

    Read defensively: this runs on the error path, where a settings object
    may be partially initialised or missing attributes.
    """
    if settings is None:
        return []
    values: list[str] = []
    for attr in (
        "groq_api_key",
        "gemini_api_key",
        "whatsapp_access_token",
        "whatsapp_phone_number_id",
        "secret_key",
        "redis_url",
        "database_url",
        "test_database_url",
    ):
        raw = getattr(settings, attr, None)
        if isinstance(raw, str) and len(raw) >= 8:
            values.append(raw)
    return values


def redact_secrets(
    text: Optional[str],
    *,
    settings: Optional[object] = None,
    extra_secrets: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """
    Return ``text`` with any recognised secret replaced by ``[redacted]``.

    Longest secrets are replaced first so a short secret that is a substring
    of a longer one cannot partially mask it.
    """
    if not text:
        return text

    result = text

    secrets = list(_configured_secrets(settings))
    if extra_secrets:
        secrets.extend(s for s in extra_secrets if isinstance(s, str) and len(s) >= 8)

    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            result = result.replace(secret, REDACTED)

    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(REDACTED, result)

    return result
