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
                    "appointments, medications, extraction_runs, "
                    "discharge_documents, patients RESTART IDENTITY CASCADE;"
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


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — Discharge document fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_storage_path(tmp_path):
    """Isolated directory for uploaded documents during tests."""
    storage = tmp_path / "discharge_documents"
    storage.mkdir(parents=True, exist_ok=True)
    return storage


@pytest.fixture
def phase2_settings(tmp_storage_path):
    """
    Settings pointing uploads at a temp directory.

    Keeps the real `storage/` tree and the developer's `.env` untouched.
    """
    return get_settings().model_copy(
        update={"document_storage_path": str(tmp_storage_path)}
    )


class FakeProvider:
    """
    Test double for `LLMProvider`.

    Returns a canned extraction payload and records the prompts it received,
    so tests can assert on prompt content without any network access.
    """

    name = "fake"

    def __init__(self, payload=None, error=None):
        self.model = "fake-model-v1"
        self._payload = payload if payload is not None else _default_extraction()
        self._error = error
        self.calls = []

    def is_configured(self):
        # Mirrors the real providers: this reflects configuration (an API
        # key being present), NOT whether a particular call succeeds.
        return True

    def extract_structured(
        self, *, system_prompt, user_prompt, json_schema, schema_name
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "json_schema": json_schema,
                "schema_name": schema_name,
            }
        )
        if self._error is not None:
            raise self._error
        return {
            "medications": [dict(m) for m in self._payload.get("medications", [])],
            "appointments": [dict(a) for a in self._payload.get("appointments", [])],
            "warning_symptoms": [
                dict(w) for w in self._payload.get("warning_symptoms", [])
            ],
        }


def _default_extraction():
    """
    A realistic extraction payload derived from a discharge summary.

    Field names mirror `app.schemas.extraction.ExtractedMedication` exactly:
    the models use `extra="ignore"`, so a typo like `name=` or
    `duration_days=` would be silently dropped rather than raising.
    """
    return {
        "patient_name": "Jane Doe",
        "hospital_name": "General Hospital",
        "discharge_date": "2026-09-20",
        "medications": [
            {
                "medication_name": "Metformin",
                "dosage": "500 mg",
                "frequency": "twice daily with meals",
                "duration": "30 days",
                "source_page": 1,
                "source_text": "Metformin 500 mg twice daily with meals",
            },
            {
                "medication_name": "Lisinopril",
                "dosage": "10 mg",
                "frequency": "once daily",
                "duration": "30 days",
                "source_page": 1,
                "source_text": "Lisinopril 10 mg once daily",
            },
        ],
        "appointments": [
            {
                "appointment_type": "Cardiology follow-up",
                "doctor_or_department": "Cardiology",
                "appointment_date": "2026-10-15",
                "location": "Main Clinic, Room 204",
                "source_page": 1,
                "source_text": "Cardiology on 2026-10-15 at Main Clinic",
            }
        ],
        "warning_symptoms": [
            {
                "symptom": "Fever above 101 F",
                "instruction": "Contact your care team immediately.",
                "source_page": 1,
                "source_text": "Fever above 101 F: contact your care team.",
            }
        ],
    }


@pytest.fixture
def fake_provider():
    """Canned provider that returns a valid, complete extraction."""
    return FakeProvider()


@pytest.fixture
def failing_provider():
    """Provider that always raises the given domain error."""
    from app.core.exceptions import ProviderTimeoutError

    return FakeProvider(error=ProviderTimeoutError("Provider timed out."))


# ── Document builders (generated in-memory; no binary fixtures on disk) ──────

DISCHARGE_TEXT = (
    "DISCHARGE SUMMARY\n"
    "\n"
    "Patient: Jane Doe\n"
    "Discharge date: 2026-09-20\n"
    "\n"
    "Medications on discharge:\n"
    "- Metformin 500 mg twice daily with meals for 30 days (new)\n"
    "- Lisinopril 10 mg once daily for 30 days (new)\n"
    "\n"
    "Follow-up appointment: Cardiology on 2026-10-15 at Main Clinic, "
    "Room 204. Bring home blood pressure log.\n"
    "\n"
    "Warning signs — seek care if you develop:\n"
    "- Fever above 101 F: contact your care team immediately.\n"
)


@pytest.fixture
def text_pdf_bytes():
    """A born-digital single-page PDF that contains a text layer."""
    import io

    import fitz

    doc = fitz.open()
    page = doc.new_page()
    # Use a monospace font so the text layer is unambiguous.
    page.insert_text((50, 70), DISCHARGE_TEXT, fontsize=9, fontname="cour")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def scanned_pdf_bytes():
    """A PDF containing only a raster image (no usable text layer)."""
    import io

    import fitz
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 300), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "DISCHARGE SUMMARY", fill="black")
    draw.text((20, 100), "Metformin 500 mg twice daily", fill="black")

    png_bytes = io.BytesIO()
    img.save(png_bytes, format="PNG")

    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(0, 0, 612, 204), stream=png_bytes.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def scan_png_bytes():
    """A single-page PNG 'scan' of a discharge summary."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 300), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "DISCHARGE SUMMARY", fill="black")
    draw.text((20, 100), "Metformin 500 mg twice daily", fill="black")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def valid_text_payload(text_pdf_bytes):
    """Multipart-compatible upload tuple for the happy path."""
    return {
        "patient_id": None,  # filled in by tests that have a patient
        "file": ("discharge-summary.pdf", text_pdf_bytes, "application/pdf"),
    }


@pytest.fixture
def document_client(db_session, phase2_settings):
    """
    Factory for a TestClient whose discharge-document service is fully
    isolated: uploads go to a temp directory and the LLM provider is a
    test double, so no test ever touches the network or the real storage dir.
    """
    from app.api.deps import get_discharge_document_service
    from app.services.discharge_document import DischargeDocumentService

    def _make(provider):
        def _override():
            yield DischargeDocumentService(
                db_session,
                settings=phase2_settings,
                llm_provider=provider,
            )

        return _override

    def _factory(provider):
        def _override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_discharge_document_service] = _make(provider)
        return TestClient(app)

    yield _factory

    app.dependency_overrides.clear()
