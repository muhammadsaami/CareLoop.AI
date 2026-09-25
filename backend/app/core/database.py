"""
CareLoop AI — Database Engine & Session Management

Uses SQLAlchemy 2.x with synchronous sessions (psycopg driver).
Sessions are dependency-injected via FastAPI's Depends mechanism.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


def _build_engine(database_url: str):
    """Create a SQLAlchemy engine with sensible connection-pool settings."""
    return create_engine(
        database_url,
        pool_pre_ping=True,       # verify connection liveness before use
        pool_size=5,
        max_overflow=10,
        echo=False,               # set True only for SQL debugging
    )


# Module-level engine — built once from settings
_settings = get_settings()
engine = _build_engine(_settings.database_url)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a database session.

    Guarantees the session is closed after the request even if an
    exception occurs.  Transactions are NOT committed automatically —
    service/repository layers must call session.commit() explicitly.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_database_connection() -> bool:
    """Return True if the database is reachable; False otherwise."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database connectivity check failed: %s", type(exc).__name__)
        return False
