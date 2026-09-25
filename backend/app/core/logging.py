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
