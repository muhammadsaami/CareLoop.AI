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
                    "TRUNCATE TABLE notifications, reminders, adherence_logs, "
                    "checkins, warning_symptoms, appointments, medications, "
                    "extraction_runs, discharge_documents, patients "
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
def rag_client(db_session, rag_settings):
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
    yield TestClient(app)
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


@pytest.fixture
def notification_client(db_session, phase5_settings, recording_provider):
    """TestClient whose notification service always uses the recording provider."""
    from app.api.deps import get_notification_service
    from app.services.notification import NotificationService

    def _override():
        yield NotificationService(
            db_session, settings=phase5_settings, provider=recording_provider
        )

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_notification_service] = _override
    client = TestClient(app)
    client.recording_provider = recording_provider  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()
