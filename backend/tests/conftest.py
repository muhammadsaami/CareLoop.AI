"""
CareLoop AI — Pytest Configuration & Test Fixtures
"""
import os
import re
import sys
import uuid
import hashlib
import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
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
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.patient import Patient
from app.models.user import AppUser
from app.notifications.base import DeliveryReceipt
from app.rag.embeddings.base import (
    STOPWORDS,
    EmbeddingProvider,
    tokenize,
)


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

#: Columns whose presence proves the test schema is not stale.  `create_all`
#: only CREATES missing tables; it never adds a column to a table that already
#: exists, so a test database created before a migration silently keeps the old
#: shape and every later insert fails with a bare ProgrammingError naming a
#: column the developer has never heard of.
#:
#: Phase 6's markers: `checkins.responses` and `escalations.id`.  When a phase
#: adds a column, add its marker here too - the alternative is a stale test
#: database that looks like dozens of unrelated failures.
_SCHEMA_MARKERS = (
    ("checkins", "responses"),
    ("checkins", "status"),
    ("escalations", "id"),
    ("escalations", "rule_code"),
    ("notifications", "escalation_id"),
    ("app_users", "password_hash"),
    ("app_users", "system_access"),
    ("app_users", "full_name"),
    ("patient_access", "relationship"),
    ("patient_access", "revoked_at"),
)


def _assert_test_schema_is_current() -> None:
    """
    Fail loudly, and at import, if the test schema predates a migration.

    Without this the symptom is a wall of `ProgrammingError`s in whatever tests
    happen to touch the changed table - which reads as a broken phase rather
    than an out-of-date test database.  The instruction is explicit about the
    remedy because there is no automatic fix that is also safe: recreating the
    schema here would silently discard whatever the developer was debugging.
    """
    import sqlalchemy as sa

    inspector = sa.inspect(test_engine)
    existing_tables = set(inspector.get_table_names())
    missing: list[str] = []
    for table, column in _SCHEMA_MARKERS:
        if table not in existing_tables:
            missing.append(f"{table} (table missing entirely)")
            continue
        columns = {col["name"] for col in inspector.get_columns(table)}
        if column not in columns:
            missing.append(f"{table}.{column}")
    if missing:
        raise RuntimeError(
            "STALE TEST DATABASE: the test schema is missing "
            f"{', '.join(missing)}.\n"
            "`create_all` does not ALTER existing tables, so a test database "
            "created before a migration keeps the old shape.\n"
            f"Recreate it:  DROP DATABASE {_TEST_DB_NAME}; "
            f"CREATE DATABASE {_TEST_DB_NAME}; then run the suite again.\n"
            f"(test url: {_redact_url(TEST_DB_URL)})"
        )


def _test_db_name() -> str:
    return TEST_DB_URL.rsplit("/", 1)[-1].split("?", 1)[0] or "careloop_test"


#: Resolved once, for the error message above.
_TEST_DB_NAME = _test_db_name()

# Ensure tables exist in the test database
Base.metadata.create_all(bind=test_engine)
_assert_test_schema_is_current()


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
            # `escalations` is listed explicitly, ahead of `checkins`, even
            # though CASCADE would reach it: the list reads as the set of tables
            # a test can write to, and a new table that is only truncated by
            # cascade is easy to forget when a test starts asserting on it.
            conn.execute(
                text(
                    "TRUNCATE TABLE notifications, escalations, reminders, "
                    "adherence_logs, checkins, warning_symptoms, appointments, "
                    "medications, extraction_runs, discharge_documents, "
                    "patient_access, app_users, patients "
                    "RESTART IDENTITY CASCADE;"
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


# ═══════════════════════════════════════════════════════════════════════════
# Authentication & patient access
# ═══════════════════════════════════════════════════════════════════════════
#
# Every API test runs as a REAL principal with a REAL signed token against the
# REAL authorization code. The alternative - overriding the auth dependency in
# tests - would leave the feature untested by the entire suite, which is how an
# authentication layer ends up "covered" while nothing verifies it.
#
# The one concession is that a patient created DURING a test is granted to the
# test principal automatically, so the pre-existing tests keep working without
# each one being edited. That grant is created by a SQLAlchemy `after_insert`
# listener rather than by fixture code, so it also covers patients built
# straight through `db_session` (the `make_patient` factory), which fixture code
# would never see.
#
# Authorization tests opt OUT of the listener, and assert on the absence of a
# grant. `_no_automatic_grants` is the opt-out switch.

#: The principal every fixture-built client authenticates as.  `None` disables
#: automatic granting, which is how an authorization test observes a principal
#: that genuinely has no access to a patient.
_AUTOGRANT_USER_ID: list = [None]


@event.listens_for(Patient, "after_insert")
def _grant_test_principal_access(mapper, connection, target):
    """
    Grant the ambient test principal access to any newly inserted `Patient`.

    Fires for every `Patient` insert, including ones made by production code
    (`POST /patients`). The route's own `grant_access` then finds the grant
    already present and returns it, so the partial unique index
    `ux_patient_access_user_patient_active` is never violated and no duplicate
    row is created.

    No-ops when no principal is registered, so the listener is inert outside an
    autogranting test.
    """
    user_id = _AUTOGRANT_USER_ID[0]
    if user_id is None:
        return
    connection.execute(
        text(
            "INSERT INTO patient_access (id, user_id, patient_id, relationship) "
            "VALUES (:id, :user_id, :patient_id, 'self') "
            "ON CONFLICT DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "patient_id": target.id,
        },
    )


@pytest.fixture
def no_automatic_grants():
    """
    Stop newly created patients from being granted to the test principal.

    Required by any test asserting that access is REFUSED: without it the
    listener hands out a grant the moment the patient row is inserted and the
    test proves nothing.
    """
    _AUTOGRANT_USER_ID[0] = None
    try:
        yield
    finally:
        _AUTOGRANT_USER_ID[0] = None


@pytest.fixture
def auth_user(db_session, no_automatic_grants):
    """
    The principal every authenticated client acts as.

    A real `AppUser` with a real bcrypt hash.  `system_access` is False by
    default, matching production, so the whole-system notification endpoints
    are refused unless a test explicitly grants operator rights.
    """
    user = AppUser(
        email=f"test-{uuid.uuid4().hex[:12]}@example.invalid",
        password_hash=hash_password("test-password-1234"),
        is_active=True,
        system_access=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    yield user
    db_session.delete(user)
    db_session.commit()


@pytest.fixture
def auth_headers(auth_user, db_session, no_automatic_grants):
    """
    Bearer headers for `auth_user`, plus automatic granting of new patients.

    Arranges the listener to fire for this principal, and restores the previous
    state afterwards so a later test cannot inherit it.
    """
    _AUTOGRANT_USER_ID[0] = auth_user.id
    try:
        yield {"Authorization": f"Bearer {create_access_token(str(auth_user.id))}"}
    finally:
        _AUTOGRANT_USER_ID[0] = None


@pytest.fixture
def operator_auth_headers(auth_user, db_session, no_automatic_grants):
    """
    Bearer headers for a principal holding `system_access`.

    `system_access` is the one global privilege, granted only by an operator in
    production (there is no route that can set it), so a test that needs it
    sets it here - on the same `auth_user` the rest of the suite uses.

    Automatic granting is enabled too, exactly as in `auth_headers`: a system
    operator still only sees patients they hold a grant for, and a test that
    creates a patient through the API must still be able to read it back.
    `system_access` widens WHICH endpoints are reachable, not which patients
    are visible - the two are independent, and conflating them is the mistake
    this comment exists to prevent.
    """
    auth_user.system_access = True
    db_session.commit()
    _AUTOGRANT_USER_ID[0] = auth_user.id
    try:
        yield {"Authorization": f"Bearer {create_access_token(str(auth_user.id))}"}
    finally:
        _AUTOGRANT_USER_ID[0] = None


def _authorized_client(headers: dict, overrides: dict | None = None):
    """
    Build a TestClient that presents `headers` on every request.

    `auth_headers` on the client rather than on each call, so the ~1,100
    existing assertions read exactly as they did before authentication existed
    and a reviewer can see that the tests did not need to change to keep
    passing - which is the point of a change like this.
    """
    test_client = TestClient(app)
    test_client.headers.update(headers)
    for key, value in (overrides or {}).items():
        app.dependency_overrides[key] = value
    return test_client


@pytest.fixture
def client(db_session, auth_headers):
    """FastAPI TestClient with overridden get_db dependency, authenticated."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with _authorized_client(auth_headers) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(db_session):
    """
    A client that presents NO credential.

    For the tests that assert 401.  It deliberately does not depend on
    `auth_headers`, so nothing about the authenticated fixtures can leak into
    it.
    """
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
def document_client(db_session, phase2_settings, auth_headers):
    """
    Factory for a TestClient whose discharge-document service is fully
    isolated: uploads go to a temp directory and the LLM provider is a
    test double, so no test ever touches the network or the real storage dir.

    Clients are authenticated; see `auth_headers` for why the suite runs as a
    real principal rather than with the auth dependency overridden.
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
        return _authorized_client(auth_headers)

    yield _factory

    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — RAG retrieval fixtures
# ═══════════════════════════════════════════════════════════════════════════


class FakeEmbeddingProvider(EmbeddingProvider):
    """
    Deterministic stand-in for a real embedding model.

    The suite must never download a 127 MB model or call an embedding API:
    that would make `pytest` slow, network-dependent, and unable to assert
    exact score thresholds.  This provider is therefore a **deterministic
    bag-of-words fingerprint**, not a semantic model - tests that need real
    semantic behaviour are marked separately and skip when the model is
    absent.

    Two properties matter for it to be a useful double:

    * It is deterministic, so a chunk's vector never changes between runs and
      an index can be re-read.
    * Its score range is *stable and known* (roughly 0.0-0.6, floor 0.10),
      so relevance-floor tests assert something real rather than an artefact.
      256 buckets and stopword removal are what keep it well behaved: at 64
      buckets, collisions made unrelated queries score higher than related
      ones, which would have made these tests assert nonsense.  1024 buckets
      go further - a 256-bucket space still collides often enough on a ~30
      token chunk to hand an unrelated query a ~0.15 score and push it over
      the floor, which is an artefact of the double rather than a real
      retrieval property.

    Its dimensions are intentionally different from both shipped providers so
    a test cannot accidentally pass because the production value happened to
    match.
    """

    name = "fake"
    default_min_score = 0.10
    dimensions_override = 1024

    def __init__(self, settings=None, *, dimensions=None):
        super().__init__(settings)
        self._dimensions = dimensions or self.dimensions_override

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def is_configured(self) -> bool:
        return True

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in tokenize(text):
            if token in STOPWORDS:
                continue
            bucket = (
                int.from_bytes(
                    hashlib.sha256(token.encode("utf-8")).digest()[:4], "big"
                )
                % self._dimensions
            )
            vector[bucket] += 1.0
        return self.l2_normalize(vector)

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @property
    def relevance_floor(self) -> float:
        return self.default_min_score


@pytest.fixture
def fake_embeddings():
    """A fresh deterministic provider, for direct unit tests."""
    return FakeEmbeddingProvider()


@pytest.fixture
def rag_settings(tmp_path):
    """
    Settings with the Chroma index pointed at a per-test temp directory.

    Every RAG test therefore gets a private, empty collection.  The real
    `chroma_data/` tree is never created, read, or written by the suite.

    The embedding provider is forced to `hashing` here purely as a
    belt-and-braces guard: the services are additionally given
    `FakeEmbeddingProvider` below, so no test can reach a model file even if
    a developer's local `.env` selects the semantic provider.
    """
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    return get_settings().model_copy(
        update={
            "chroma_persist_directory": str(chroma_dir),
            "chroma_collection_name": "careloop_documents",
            "rag_embedding_provider": "hashing",
        }
    )


@pytest.fixture(autouse=True)
def _reset_chroma_clients():
    """
    Drop chroma's process-global client cache around every test.

    `chromadb.PersistentClient` is a process-wide singleton keyed on its
    settings, so without this a test could inherit a client pointing at a
    previous test's temp directory and read another test's vectors.
    """
    from app.rag.vector_store import reset_client_cache

    reset_client_cache()
    yield
    reset_client_cache()


@pytest.fixture
def rag_client(db_session, rag_settings, auth_headers):
    """
    TestClient whose RAG services are fully isolated.

    Both services receive the per-test settings, so the vector store and
    chunker resolve against the temp Chroma directory, and both are handed
    `FakeEmbeddingProvider` so no test downloads a model or opens a socket.
    Nothing here touches the network or the developer's real index.
    """
    from app.api.deps import (
        get_rag_indexing_service,
        get_rag_retrieval_service,
    )
    from app.rag.indexing import RagIndexingService
    from app.rag.retrieval import RagRetrievalService

    embeddings = FakeEmbeddingProvider(rag_settings)

    def _override_indexing():
        yield RagIndexingService(
            db_session, settings=rag_settings, embedding_provider=embeddings
        )

    def _override_retrieval():
        yield RagRetrievalService(
            db_session, settings=rag_settings, embedding_provider=embeddings
        )

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_rag_indexing_service] = _override_indexing
    app.dependency_overrides[get_rag_retrieval_service] = _override_retrieval
    yield _authorized_client(auth_headers)
    app.dependency_overrides.clear()


@pytest.fixture
def make_patient(db_session):
    """Create a Patient row directly and return it."""
    from app.models.patient import Patient

    def _factory(name="Test Patient", suffix="1"):
        patient = Patient(
            name=name,
            contact_number=f"+1-555-{suffix}",
            caregiver_contact=f"+1-556-{suffix}",
        )
        db_session.add(patient)
        db_session.flush()
        db_session.refresh(patient)
        return patient

    return _factory


@pytest.fixture
def make_document(db_session):
    """
    Create a DischargeDocument row with realistic page-marked extracted text.

    Bypasses the upload pipeline on purpose: RAG tests are about what happens
    to text that ALREADY exists, and going through OCR/extraction would add
    network and timing dependencies without testing anything RAG-specific.
    """
    from app.models.discharge_document import (
        DischargeDocument,
        DocumentType,
        ProcessingStatus,
    )

    #: Two pages, each with content a query can actually match.
    SAMPLE_TEXT = (
        "--- PAGE 1 ---\n"
        "Discharge summary for the patient following a planned hip replacement.\n\n"
        "Medication on discharge: Paracetamol 500 mg to be taken orally every six "
        "hours as required for pain relief. Continue for five days.\n\n"
        "The wound dressing should be changed once daily and the area kept dry.\n"
        "--- PAGE 2 ---\n"
        "Follow up appointment with the orthopaedic team in fourteen days.\n"
        "Physiotherapy exercises are to begin on the second day after discharge.\n"
        "Contact the ward if there is a temperature above 38 degrees Celsius.\n"
    )

    def _factory(
        patient,
        *,
        extracted_text=SAMPLE_TEXT,
        processing_status=ProcessingStatus.COMPLETED,
        filename="discharge-summary.pdf",
        sha256=None,
    ):
        document = DischargeDocument(
            patient_id=patient.id,
            original_filename=filename,
            stored_filename=f"{patient.id}-{filename}",
            file_path=f"/tmp/{filename}",
            content_type="application/pdf",
            file_size=1024,
            sha256_hash=sha256 or f"{abs(hash((patient.id, filename))) % (10**32):032d}",
            document_type=DocumentType.DISCHARGE_SUMMARY,
            processing_status=processing_status,
            page_count=2 if extracted_text else 0,
            extracted_text=extracted_text,
        )
        db_session.add(document)
        db_session.flush()
        db_session.refresh(document)
        return document

    _factory.SAMPLE_TEXT = SAMPLE_TEXT
    return _factory


@pytest.fixture
def indexed_document(rag_client, make_patient, make_document):
    """
    A COMPLETED document that has already been indexed.

    Returns `(patient, document, index_response_json)`.
    """
    patient = make_patient(name="Indexed Patient", suffix="0100")
    document = make_document(patient)
    response = rag_client.post(
        f"/api/v1/rag/documents/{document.id}/index",
        json={"patient_id": str(patient.id)},
    )
    assert response.status_code == 200, response.text
    return patient, document, response.json()


# -- Phase 4: LangGraph grounded-answer agent ---------------------------------
#
# The agent reuses the Phase 3 retrieval service and the Phase 2 provider
# seam, so the fixtures below compose the SAME fakes those phases already use
# rather than introducing a parallel mocking layer.  `FakeProvider` is
# subclassed only to return an answer-shaped payload; the retrieval path, the
# embedding provider, the Chroma temp directory, and the database session are
# all the real Phase 3 objects.


class FakeAnswerProvider(FakeProvider):
    """
    `LLMProvider` double that returns a grounded-answer payload.

    Keeps the Phase 2 recording behaviour (`.calls` holds the prompts) so a
    test can assert the model was shown the real chunk ids and nothing else.
    """

    name = "fake-answer"

    #: Distinguishes "caller passed no payload" from "caller passed None",
    #: so a test can hand the agent a literal `None` as malformed output.
    _UNSET = object()

    def __init__(self, payload=_UNSET, error=None, configured=True):
        self.model = "fake-answer-model-v1"
        self._payload = (
            _default_answer() if payload is self._UNSET else payload
        )
        self._error = error
        self._configured = configured
        self.calls = []

    def is_configured(self):
        # Separable from `_error` so a test can distinguish "no API key
        # configured" (503 path) from "the call failed" (502 path).
        return self._configured

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
        # Returned as-is when it is not a mapping, so a test can hand the agent
        # a deliberately malformed payload (None, a string, a list) and exercise
        # the real failure path. Dicts are copied so a caller mutating the
        # result cannot affect a later call.
        if isinstance(self._payload, dict):
            return dict(self._payload)
        return self._payload


def _default_answer():
    """
    A well-formed answer payload.

    `cited_chunk_ids` is deliberately EMPTY here: the real chunk ids are only
    known at test time (they depend on the embedding provider's hashing), so a
    test that wants a supported answer must fill them in.  Returning this as-is
    models the honest refusal, which is the safe default for a fixture.
    """
    return {
        "answer": None,
        "supported": False,
        "cited_chunk_ids": [],
        "model_declined_reason": "no default payload configured for this test",
    }


@pytest.fixture
def answer_provider():
    """Provider that returns a well-formed refusal."""
    return FakeAnswerProvider()


@pytest.fixture
def unconfigured_provider():
    """Provider reporting `is_configured() == False` (the 503 path)."""
    return FakeAnswerProvider(configured=False)


@pytest.fixture
def failing_answer_provider():
    """Provider that always raises a domain error (the 502 path)."""
    from app.core.exceptions import ProviderTimeoutError

    return FakeAnswerProvider(error=ProviderTimeoutError("Provider timed out."))


@pytest.fixture
def agent_client(db_session, rag_settings, rag_client):
    """
    TestClient with the agent fully isolated and the LLM always mocked.

    `rag_client` is depended on for its side effects: it has already installed
    the Phase 3 retrieval override (real `RagRetrievalService`, temp Chroma
    directory, `FakeEmbeddingProvider`) and the database override, so the
    agent inherits the Phase 3 boundary rather than a test-only bypass of it.

    The provider override is what guarantees no test can reach Groq or Gemini:
    `get_grounded_response_generator` takes the same `LLMProviderDep` seam
    Phase 2 uses, so overriding it here is sufficient.
    """
    from app.api.deps import get_grounded_response_generator
    from app.agent.generation import GroundedResponseGenerator

    holder = {"provider": FakeAnswerProvider()}

    def _override_generator():
        yield GroundedResponseGenerator(
            settings=rag_settings, llm_provider=holder["provider"]
        )

    app.dependency_overrides[get_grounded_response_generator] = (
        _override_generator
    )
    rag_client.agent_provider = holder["provider"]  # type: ignore[attr-defined]
    rag_client.agent_settings = rag_settings  # type: ignore[attr-defined]
    yield rag_client
    app.dependency_overrides.clear()


@pytest.fixture
def set_agent_answer(agent_client):
    """
    Replace the agent's provider payload mid-test.

    Returns a setter so a test can install a supported answer, a fabricated
    citation, a malformed payload, or a failure - without rebuilding the
    dependency graph.
    """
    from app.agent.generation import GroundedResponseGenerator
    from app.api.deps import get_grounded_response_generator

    def _setter(payload=None, *, error=None, configured=True, provider=None):
        holder = provider or FakeAnswerProvider(
            payload=payload, error=error, configured=configured
        )
        agent_client.agent_provider = holder  # type: ignore[attr-defined]

        def _override():
            yield GroundedResponseGenerator(
                settings=agent_client.agent_settings,  # type: ignore[attr-defined]
                llm_provider=holder,
            )

        app.dependency_overrides[get_grounded_response_generator] = (
            _override
        )
        return holder

    yield _setter
    app.dependency_overrides.pop(get_grounded_response_generator, None)


@pytest.fixture
def agent_fixtures(rag_client, db_session, make_patient, make_document, rag_settings):
    """
    Everything needed to drive the agent directly, without HTTP.

    Returns a dict with an indexed patient/document pair, a real Phase 3
    `RagRetrievalService`, a fake provider, and a real `AgentService` wired to
    both.  Used by the unit-level tests; the API tests use `agent_client`.
    """
    from app.agent.generation import GroundedResponseGenerator
    from app.agent.safety import AgentSafetyValidator
    from app.agent.service import AgentService
    from app.rag.indexing import RagIndexingService
    from app.rag.retrieval import RagRetrievalService
    from tests.conftest import FakeEmbeddingProvider

    embeddings = FakeEmbeddingProvider(rag_settings)
    patient = make_patient(name="Agent Patient", suffix="0400")
    document = make_document(patient)

    RagIndexingService(
        db_session, settings=rag_settings, embedding_provider=embeddings
    ).index_document(patient_id=patient.id, document_id=document.id)

    retrieval = RagRetrievalService(
        db_session, settings=rag_settings, embedding_provider=embeddings
    )
    provider = FakeAnswerProvider()
    service = AgentService(
        retrieval=retrieval,
        generator=GroundedResponseGenerator(
            settings=rag_settings, llm_provider=provider
        ),
        validator=AgentSafetyValidator(settings=rag_settings),
        settings=rag_settings,
    )
    return {
        "patient": patient,
        "document": document,
        "retrieval": retrieval,
        "provider": provider,
        "service": service,
        "settings": rag_settings,
        "db_session": db_session,
    }


# -- Phase 5: Scheduling & notifications ---------------------------------------
#
# The console provider is the default NOTIFICATION_PROVIDER, so delivery is
# inert unless a test opts in.  `RecordingNotificationProvider` below makes that
# explicit: it counts sends so a test can assert a reminder reached the
# transport exactly once, which is how the idempotency guarantee is verified.


class RecordingNotificationProvider:
    """
    `NotificationProvider` double that records sends in memory.

    Mirrors the real provider contract - including raising the domain
    transient/permanent errors - so the retry policy is exercised through the
    production code path rather than by stubbing the service.

    `fail_times` drives the failure sequence: 2 means the first two sends raise
    `transient_error` and the third succeeds, which is what the retry tests
    need.
    """

    channel = "console"

    def __init__(self, *, fail_times=0, transient_error=None, permanent_error=None):
        self.sends = []
        self._fail_times = fail_times
        self._transient_error = transient_error
        self._permanent_error = permanent_error

    def is_configured(self):
        return True

    @property
    def send_count(self) -> int:
        return len(self.sends)

    def send(self, *, recipient: str, body: str):
        from app.core.exceptions import (
            NotificationPermanentError,
            NotificationTransientError,
        )

        # Recorded before the failure is raised, so a test can see that an
        # attempt was actually made.
        self.sends.append({"recipient": recipient, "body": body})
        if self._permanent_error is not None:
            raise self._permanent_error
        if self._transient_error is not None and self._fail_times > 0:
            self._fail_times -= 1
            raise self._transient_error
        return DeliveryReceipt(provider_message_id="recorded-1", status="recorded")


@pytest.fixture
def recording_provider():
    """A provider that always succeeds and counts every send."""
    return RecordingNotificationProvider()


@pytest.fixture
def phase5_settings():
    """Settings with fast, deterministic retry timings for the suite."""
    return get_settings().model_copy(
        update={
            "notification_provider": "console",
            "notification_max_attempts": 3,
            "notification_retry_base_seconds": 1,
            "notification_retry_max_seconds": 4,
            "scheduler_lookahead_seconds": 300,
            "scheduler_batch_size": 100,
        }
    )


@pytest.fixture
def make_medication(db_session):
    """Create a Medication row directly, with a realistic free-text frequency."""

    from app.models.medication import Medication

    def _factory(
        patient,
        *,
        name="Metformin",
        dosage="500 mg",
        frequency="twice daily with meals",
    ):
        medication = Medication(
            patient_id=patient.id,
            name=name,
            dosage=dosage,
            frequency=frequency,
        )
        db_session.add(medication)
        db_session.flush()
        db_session.refresh(medication)
        return medication

    return _factory


@pytest.fixture
def make_appointment(db_session):
    """Create an Appointment row with a timezone-aware date."""

    from datetime import timedelta

    from app.core.timezones import utcnow
    from app.models.appointment import Appointment, AppointmentStatus

    def _factory(patient, *, when=None, doctor_name="Dr. House"):
        appointment = Appointment(
            patient_id=patient.id,
            doctor_name=doctor_name,
            date=when or (utcnow() + timedelta(days=7)),
            status=AppointmentStatus.scheduled,
        )
        db_session.add(appointment)
        db_session.flush()
        db_session.refresh(appointment)
        return appointment

    return _factory


@pytest.fixture
def phase5_services(db_session, phase5_settings, recording_provider):
    """
    Phase 5 services wired to the recording provider.

    Everything the suite needs to drive scheduling directly, with no HTTP and
    no network.  The provider is injected so the delivery path is real code.
    """
    from app.services.notification import NotificationService
    from app.services.reminder import ReminderService
    from app.services.scheduler import SchedulerService

    reminder_service = ReminderService(db_session)
    notification_service = NotificationService(
        db_session, settings=phase5_settings, provider=recording_provider
    )
    scheduler_service = SchedulerService(
        db_session, settings=phase5_settings
    )
    # The scheduler builds its own NotificationService; make sure it uses the
    # same recording provider so a dispatch-then-deliver test sees the sends.
    scheduler_service._notification_service = notification_service

    return {
        "db": db_session,
        "settings": phase5_settings,
        "provider": recording_provider,
        "reminders": reminder_service,
        "notifications": notification_service,
        "scheduler": scheduler_service,
    }


def _install_notification_overrides(db_session, settings, provider):
    """Point the notification service at the recording provider."""
    from app.api.deps import get_notification_service
    from app.services.notification import NotificationService

    def _override():
        yield NotificationService(
            db_session, settings=settings, provider=provider
        )

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_notification_service] = _override


@pytest.fixture
def notification_client(db_session, phase5_settings, recording_provider, auth_headers):
    """
    TestClient whose notification service always uses the recording provider.

    Authenticates as an ordinary principal: `system_access` is off, matching
    production. Tests exercising the two whole-system endpoints
    (`/notifications/dispatch`, `/notifications/retry`) need
    `operator_notification_client` instead - which is the point, because those
    endpoints walk every patient's rows and cannot be authorised by a
    per-patient grant.
    """
    _install_notification_overrides(db_session, phase5_settings, recording_provider)
    client = _authorized_client(auth_headers)
    client.recording_provider = recording_provider  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def operator_notification_client(
    db_session, phase5_settings, recording_provider, operator_auth_headers
):
    """`notification_client` for a principal holding `system_access`."""
    _install_notification_overrides(db_session, phase5_settings, recording_provider)
    client = _authorized_client(operator_auth_headers)
    client.recording_provider = recording_provider  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6 — Daily check-ins & escalation
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def phase6_settings():
    """
    Phase 6 settings with every rule ACTIVE and notifications ENABLED.

    Explicit rather than inherited: a test that exercises an escalation should
    fail because of the rule it is testing, not because a deployment default
    happened to be off.  Tests that want the pathway disabled override these
    keys themselves.
    """
    return get_settings().model_copy(
        update={
            "checkin_escalation_enabled": True,
            "checkin_notify_caregiver": True,
            "checkin_enabled_rules": "",
            "checkin_severity_floor": "low",
            "checkin_prompt_local_time": "09:00",
            "checkin_max_symptom_reports": 20,
        }
    )


@pytest.fixture
def make_warning_symptom(db_session):
    """
    Create a `WarningSymptom` for a patient.

    `description` is realistic discharge-document prose, because the whole
    design rests on NOT matching that text: a rule must fire from the stored
    severity and the patient's coded answer, and must be identical however
    differently the description is worded.
    """

    from app.models.warning_symptom import SymptomSeverity, WarningSymptom

    def _factory(
        patient,
        *,
        description="Increased breathlessness on exertion",
        severity=SymptomSeverity.high,
    ):
        symptom = WarningSymptom(
            patient_id=patient.id,
            description=description,
            severity=severity,
        )
        db_session.add(symptom)
        db_session.flush()
        db_session.refresh(symptom)
        return symptom

    return _factory


@pytest.fixture
def phase6_services(db_session, phase6_settings, recording_provider):
    """
    Phase 6 services wired to the recording provider.

    The `NotificationService` is injected into `EscalationService` explicitly.
    Without that, the escalation service would build its own notification
    service from settings and a test would have no seam to observe or control
    the caregiver send - the delivery path would be real code, but invisible.
    """
    from app.services.daily_checkin import DailyCheckInService
    from app.services.escalation import EscalationService
    from app.services.notification import NotificationService
    from app.services.reminder import ReminderService
    from app.services.scheduler import SchedulerService

    notification_service = NotificationService(
        db_session, settings=phase6_settings, provider=recording_provider
    )
    escalation_service = EscalationService(
        db_session,
        settings=phase6_settings,
        notification_service=notification_service,
    )
    scheduler_service = SchedulerService(db_session, settings=phase6_settings)
    # The scheduler builds its own NotificationService; point it at the
    # recording one so a dispatch-then-deliver test sees the sends.
    scheduler_service._notification_service = notification_service

    return {
        "db": db_session,
        "settings": phase6_settings,
        "provider": recording_provider,
        "notifications": notification_service,
        "escalations": escalation_service,
        "reminders": ReminderService(db_session, settings=phase6_settings),
        "scheduler": scheduler_service,
        "checkins": DailyCheckInService(
            db_session,
            settings=phase6_settings,
            escalation_service=escalation_service,
        ),
    }


@pytest.fixture
def checkin_client(db_session, phase6_settings, recording_provider, auth_headers):
    """
    TestClient for the Phase 6 endpoints.

    BOTH Phase 6 services are overridden, not just the notification one.  The
    escalation dependency has to be overridden as well, because it is the thing
    that owns the notification service - overriding only the notification
    dependency would leave the check-in route building an escalation service
    with its own un-recording provider.
    """
    from app.api.deps import (
        get_daily_checkin_service,
        get_escalation_service,
        get_notification_service,
        get_reminder_service,
    )
    from app.services.daily_checkin import DailyCheckInService
    from app.services.escalation import EscalationService
    from app.services.notification import NotificationService
    from app.services.reminder import ReminderService

    def _notifications():
        yield NotificationService(
            db_session, settings=phase6_settings, provider=recording_provider
        )

    def _escalations():
        yield EscalationService(
            db_session,
            settings=phase6_settings,
            notification_service=NotificationService(
                db_session,
                settings=phase6_settings,
                provider=recording_provider,
            ),
        )

    def _checkins():
        yield DailyCheckInService(
            db_session,
            settings=phase6_settings,
            escalation_service=EscalationService(
                db_session,
                settings=phase6_settings,
                notification_service=NotificationService(
                    db_session,
                    settings=phase6_settings,
                    provider=recording_provider,
                ),
            ),
        )

    def _reminders():
        yield ReminderService(db_session, settings=phase6_settings)

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_notification_service] = _notifications
    app.dependency_overrides[get_escalation_service] = _escalations
    app.dependency_overrides[get_daily_checkin_service] = _checkins
    app.dependency_overrides[get_reminder_service] = _reminders
    client = _authorized_client(auth_headers)
    client.recording_provider = recording_provider  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()
