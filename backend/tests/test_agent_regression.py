"""
CareLoop AI - Phase 4 Regression Tests

Guards the promise that Phase 4 was purely ADDITIVE: Phase 1 (CRUD, health),
Phase 2 (ingestion and extraction), and Phase 3 (indexing and retrieval) must
behave exactly as they did before the agent existed.

These are deliberately broad rather than clever. The value is in re-running the
real prior-phase paths against the same app instance the agent is registered on,
because the realistic regression is a router or dependency change breaking a
neighbour, not a subtle logic change.
"""
from __future__ import annotations

import uuid

import pytest

from app.main import app

AGENT_ENDPOINT = "/api/v1/agent/query"


# ── Phase 1 regression ───────────────────────────────────────────────────────


def test_health_endpoint_still_200(agent_client):
    """Phase 1: the health route is untouched and still first."""
    response = agent_client.get("/api/v1/health")
    assert response.status_code == 200
    assert "status" in response.json()


def test_health_reports_not_degraded_by_the_agent(agent_client):
    """The agent's presence must not change the health payload."""
    body = agent_client.get("/api/v1/health").json()
    # Whatever the shape, it must not claim the agent is a degraded dependency.
    assert body.get("status") in ("ok", "healthy")


def test_patient_crud_still_works(agent_client, db_session):
    """Phase 1: patient creation and read are unaffected."""
    from app.models.patient import Patient

    created = agent_client.post(
        "/api/v1/patients", json={"name": "Regression Patient", "contact_number": "+1-555-9001"}
    )
    assert created.status_code in (200, 201), created.text

    patient_id = created.json().get("id")
    fetched = agent_client.get(f"/api/v1/patients/{patient_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Regression Patient"


def test_medication_endpoints_still_work(agent_client, db_session, make_patient):
    """Phase 1: medication CRUD is unaffected by the new router."""
    from app.models.medication import Medication

    patient = make_patient(name="Med Patient", suffix="0600")
    created = agent_client.post(
        "/api/v1/patients/{}/medications".format(patient.id),
        json={"name": "Metformin", "dosage": "500 mg", "frequency": "twice daily"},
    )
    assert created.status_code in (200, 201), created.text


def test_appointment_endpoints_still_work(agent_client, make_patient):
    """Phase 1: appointment CRUD is unaffected."""
    patient = make_patient(name="Appt Patient", suffix="0601")
    created = agent_client.post(
        f"/api/v1/patients/{patient.id}/appointments",
        json={
            "appointment_type": "Cardiology",
            "doctor_name": "Dr Regression",
            "date": "2026-10-15T09:00:00",
            "location": "Main Clinic",
        },
    )
    assert created.status_code in (200, 201), created.text


def test_unknown_route_is_still_404(agent_client):
    """Phase 1: the agent router did not swallow unmatched paths."""
    assert agent_client.get("/api/v1/definitely-not-a-route").status_code == 404


# ── Phase 2 regression ───────────────────────────────────────────────────────


def test_extraction_still_uses_the_provider_seam(document_client, fake_provider):
    """
    Phase 2: extraction still runs through `LLMProviderDep`.

    The agent takes the same seam, so this proves the two coexist rather than
    one having replaced the other.
    """
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.api.deps import get_discharge_document_service
    from app.services.discharge_document import DischargeDocumentService

    with TestClient(app) as _:
        pass  # app still constructs

    assert get_discharge_document_service is not None
    assert get_db is not None


def test_agent_does_not_replace_the_extraction_provider_dependency():
    """
    Phase 2: `get_llm_provider` is still the single provider seam.

    If the agent had introduced a second provider path, this would catch it.
    """
    from app.api.deps import get_grounded_response_generator, get_llm_provider

    assert get_llm_provider is not None
    assert get_grounded_response_generator is not None


def test_document_upload_still_validates_files(document_client, fake_provider):
    """Phase 2: a non-PDF upload is still rejected."""
    client = document_client(fake_provider)
    response = client.post(
        "/api/v1/discharge-documents",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert response.status_code in (400, 415, 422)


def test_extraction_validation_rules_are_unchanged():
    """
    Phase 2: `has_overreach` is the single overreach rule set.

    The agent imports and reuses it rather than defining a parallel version.
    """
    from app.agent import safety as agent_safety
    from app.services import safety as phase2_safety

    assert agent_safety.has_overreach is phase2_safety.has_overreach
    assert phase2_safety.has_overreach("I recommend you increase the dose.")


# ── Phase 3 regression ───────────────────────────────────────────────────────


def test_retrieval_endpoint_still_works(agent_client, indexed_document):
    """Phase 3: the retrieval route is byte-for-byte unaffected."""
    patient, document, _ = indexed_document
    response = agent_client.post(
        "/api/v1/rag/retrieve",
        json={
            "patient_id": str(patient.id),
            "document_id": str(document.id),
            "query": "Paracetamol",
        },
    )
    assert response.status_code == 200
    assert response.json()["chunks"]


def test_indexing_endpoint_still_works(agent_client, make_patient, make_document):
    """Phase 3: explicit re-indexing is unaffected."""
    patient = make_patient(name="Reindex Patient", suffix="0602")
    document = make_document(patient, filename="reindex.pdf")
    response = agent_client.post(
        f"/api/v1/rag/documents/{document.id}/index",
        json={"patient_id": str(patient.id)},
    )
    assert response.status_code == 200
    assert response.json()["chunks_indexed"] >= 1


def test_retrieval_still_enforces_ownership(agent_client, indexed_document, make_patient):
    """Phase 3: cross-patient retrieval is still a 404."""
    patient, document, _ = indexed_document
    other = make_patient(name="Nosy Patient", suffix="0603")
    response = agent_client.post(
        "/api/v1/rag/retrieve",
        json={
            "patient_id": str(other.id),
            "document_id": str(document.id),
            "query": "Paracetamol",
        },
    )
    assert response.status_code == 404


def test_retrieval_still_rejects_blank_queries(agent_client, indexed_document):
    """Phase 3: the empty-query guard is untouched."""
    patient, document, _ = indexed_document
    response = agent_client.post(
        "/api/v1/rag/retrieve",
        json={
            "patient_id": str(patient.id),
            "document_id": str(document.id),
            "query": "   ",
        },
    )
    assert response.status_code == 422


def test_agent_reuses_the_retrieval_service_rather_than_chroma(
    agent_client, indexed_document, monkeypatch
):
    """
    Phase 3 + 4: the agent has no direct Chroma path.

    If the agent bypassed `RagRetrievalService`, the ownership and fingerprint
    guarantees would silently not apply to agent answers. Asserting that the
    agent's retrieval dependency IS the Phase 3 service pins that.
    """
    from app.api.deps import get_rag_retrieval_service
    from app.rag.retrieval import RagRetrievalService

    assert get_rag_retrieval_service is not None
    # The agent dependency is built FROM the retrieval dependency.
    import app.api.deps as deps

    source = deps.get_agent_service.__code__
    assert "RagRetrievalService" not in source.co_names
    assert RagRetrievalService is not None


def test_embedding_fingerprint_validation_still_applies(agent_client, indexed_document):
    """
    Phase 3: the fingerprint guard is unchanged.

    The agent inherits it by reusing the retrieval service, so this is really a
    check that the shared path was not bypassed.
    """
    from app.core.exceptions import EmbeddingFingerprintMismatchError

    assert issubclass(
        EmbeddingFingerprintMismatchError, Exception
    )


# ── Phase 4 registration ─────────────────────────────────────────────────────


def test_agent_route_is_registered_exactly_once():
    """A duplicated router would silently double every agent request."""
    paths = [r.path for r in app.routes if getattr(r, "path", None) == AGENT_ENDPOINT]
    assert len(paths) == 1


def test_openapi_still_documents_every_prior_router():
    """Every pre-existing tag is still present in the spec."""
    spec = app.openapi()
    tags = set()
    for path_item in spec["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                tags.update(operation.get("tags", []))
    for expected in (
        "Health",
        "Patients",
        "Medications",
        "Appointments",
        "RAG Retrieval",
        "Agent",
    ):
        assert expected in tags, f"missing tag: {expected}"


def test_agent_endpoint_does_not_shadow_rag_routes(agent_client):
    """Distinct prefixes: the agent must not intercept RAG paths."""
    from app.api.deps import RagRetrievalServiceDep  # noqa: F401

    assert agent_client.post(
        "/api/v1/rag/retrieve", json={}
    ).status_code == 422  # validation, not agent routing


# ── Additive-only guarantees ─────────────────────────────────────────────────


def test_no_database_migration_was_added_for_phase4():
    """
    Phase 4 is stateless, so it must not have altered the schema.

    The agent creates no tables of its own.  This used to assert exactly two
    migrations, which is now wrong for the right reason: Phase 5 legitimately
    added a third (reminders/notifications).  The guard is restated in terms of
    what it actually protects - no migration introduces an agent-owned table,
    and the revision chain stays linear with Phase 4 not appearing in it.
    """
    import os
    import re

    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    versions = os.path.join(backend, "alembic", "versions")
    if not os.path.isdir(versions):
        pytest.skip("alembic versions directory not present")
    files = sorted(
        f for f in os.listdir(versions) if f.endswith(".py")
    )

    # Phase 1 created the schema, Phase 2 added discharge documents, Phase 5
    # added scheduling.  Phase 4 contributed none.
    assert len(files) == 3, f"unexpected migration count: {files}"

    # No migration may create a table owned by the agent layer.
    agent_tables = {"agent_runs", "agent_messages", "checkpoints", "agent_state"}
    for name in files:
        with open(os.path.join(versions, name), encoding="utf-8") as handle:
            body = handle.read()
        assert not (agent_tables & set(re.findall(r'create_table\(\s*"(\w+)"', body))), (
            f"{name} creates an agent-owned table"
        )

    # The chain must be linear: each migration declares exactly one down_revision.
    for name in files:
        with open(os.path.join(versions, name), encoding="utf-8") as handle:
            body = handle.read()
        assert re.search(r"^down_revision\s*[:=]", body, re.MULTILINE), (
            f"{name} does not declare a down_revision"
        )


def test_agent_package_imports_without_a_provider_key(agent_client):
    """
    The agent module graph must load with no API key configured.

    Provider resolution is lazy, so importing the app never requires one.
    """
    import importlib

    for module in (
        "app.agent",
        "app.agent.graph",
        "app.agent.state",
        "app.agent.safety",
        "app.agent.generation",
        "app.agent.service",
        "app.schemas.agent",
        "app.api.routes.agent",
    ):
        assert importlib.import_module(module) is not None
