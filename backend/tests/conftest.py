"""
CareLoop AI — Pytest Configuration & Test Fixtures
"""
import os
import re
import sys
import uuid
import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from starlette.testclient import TestClient

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Load backend/.env into the process environment so that environment
# configuration is available to the test suite.  Real environment variables
# always take precedence over values from .env (override=False).
load_dotenv(os.path.join(backend_dir, ".env"), override=False)

# Determine test database URL.
# TEST_DATABASE_URL is REQUIRED and must come from environment
# configuration (process environment or backend/.env).  There is
# deliberately NO hardcoded credential fallback.
#
# This check runs BEFORE importing the application package so that a missing
# test database URL surfaces this actionable message rather than a downstream
# configuration error from app.core.
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DB_URL:
    raise RuntimeError(
        "CONFIGURATION ERROR: TEST_DATABASE_URL is not set.\n"
        "The test suite requires an explicit, isolated test database and will "
        "not fall back to any hardcoded credentials.\n"
        "Fix one of the following:\n"
        "  1. Add TEST_DATABASE_URL to backend/.env (see "
        "backend/.env.example)\n"
        "  2. Export TEST_DATABASE_URL in your shell before running pytest"
    )

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app


def _redact_url(url: str) -> str:
    """Return a print-safe URL with any embedded password masked.

    Database URLs are credentials.  They must never appear verbatim in
    error messages, logs, or test output.
    """
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", url)


# Safety check: Ensure test DB is distinct from development/production DB
settings = get_settings()
if TEST_DB_URL == settings.database_url and not settings.is_testing:
    # If the user points TEST_DATABASE_URL to the dev db, safety check warning
    if not TEST_DB_URL.endswith("_test"):
        raise RuntimeError(
            f"SAFETY ERROR: Test database ({_redact_url(TEST_DB_URL)}) must be "
            f"distinct from the primary database "
            f"({_redact_url(settings.database_url)})!"
        )

test_engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(
    bind=test_engine, autocommit=False, autoflush=False, class_=Session
)

# Ensure tables exist in the test database
Base.metadata.create_all(bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Ensure test tables are created before running any test."""
    Base.metadata.create_all(bind=test_engine)
    yield
    # Optional cleanup


@pytest.fixture(autouse=True)
def clean_database():
    """Clean all tables between tests to guarantee test isolation."""
    yield
    with test_engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    "TRUNCATE TABLE adherence_logs, checkins, warning_symptoms, "
                    "appointments, medications, patients RESTART IDENTITY CASCADE;"
                )
            )


@pytest.fixture
def db_session():
    """Provide a direct database session for test verification."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI TestClient with overridden get_db dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def sample_patient(client):
    """Helper fixture to create a valid patient and return response data."""
    payload = {
        "name": "Jane Doe",
        "contact_number": "+1-555-0199",
        "caregiver_contact": "+1-555-0188",
        "discharge_date": "2026-09-20",
    }
    response = client.post("/api/v1/patients", json=payload)
    assert response.status_code == 201
    return response.json()
