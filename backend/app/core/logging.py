"""
CareLoop AI — Structured Logging Foundation

Rules:
- Never log passwords, tokens, API keys, or database credentials.
- Never log full discharge summaries, medication histories, or check-in responses.
- Log enough to debug request failures and database errors.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root logger with a consistent structured format."""
    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers on re-import
    if not root_logger.handlers:
        root_logger.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.  Use module __name__ as the name."""
    return logging.getLogger(name)


def log_exception_without_phi(
    logger: logging.Logger,
    message: str,
    exc: BaseException,
    *,
    level: int = logging.ERROR,
    **fields: object,
) -> None:
    """
    Log a failure by *type* only - no traceback, no exception message.

    Why this exists, since `logger.exception` looks like the obvious choice:

    `logger.exception` (and `exc_info=True`) appends the formatted traceback, and
    a traceback ends with the exception's own message.  That message is very
    often the single place a third-party or database failure leaks something we
    promised never to log - a WhatsApp 4xx body echoing the recipient number
    back, a `psycopg` error quoting the offending value, a `KeyError` naming a
    column that happened to hold a phone number, or an application error that
    interpolated a medication name.  All of that lands in the log verbatim.

    The exception *type* is enough to triage, and every call site re-raises, so
    the failure is still surfaced to Celery and the caller.

    The missing traceback is intentional.  Please do not "fix" this by going
    back to `logger.exception`; if a stack is genuinely needed, emit it from a
    reviewed code path that has already established the payload is safe.
    """
    extra = " ".join(f"{key}={value}" for key, value in fields.items())
    suffix = f" {extra}" if extra else ""
    logger.log(
        level,
        "%s error_type=%s%s",
        message,
        type(exc).__name__,
        suffix,
    )
