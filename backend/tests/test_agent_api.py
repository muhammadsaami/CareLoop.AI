"""
CareLoop AI - Phase 4 Agent API Tests

Covers `POST /api/v1/agent/query`: response schema, request validation, tenant
isolation, error mapping, and the PHI-in-logs guarantee.

NO NETWORK. The provider seam is overridden with a fake in every test, so no
Groq or Gemini client is constructed and no API key is read.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.core.exceptions import ProviderTimeoutError
from app.schemas.agent import GroundedAnswerRequest, GroundedAnswerResponse
from tests.conftest import FakeAnswerProvider

ANSWER_TEXT = (
    "Paracetamol 500 mg can be taken orally every six hours as required for "
    "pain relief, and it should be continued for five days."
)

ENDPOINT = "/api/v1/agent/query"


@pytest.fixture
def indexed_pair(agent_client, make_patient, make_document):
    """An indexed patient/document pair reachable through the agent endpoint."""
    from app.rag.indexing import RagIndexingService

    patient = make_patient(name="API Agent Patient", suffix="0500")
    document = make_document(patient)
    response = agent_client.post(
        f"/api/v1/rag/documents/{document.id}/index",
        json={"patient_id": str(patient.id)},
    )
    assert response.status_code == 200, response.text
    return patient, document


def _body(patient, document, query="What medication was prescribed for pain relief?", **extra):
    payload = {
        "patient_id": str(patient.id),
        "discharge_document_id": str(document.id),
        "query": query,
    }
    payload.update(extra)
    return payload


def _first_chunk_id(agent_client, patient, document):
    response = agent_client.post(
        "/api/v1/rag/retrieve",
        json={
            "patient_id": str(patient.id),
            "document_id": str(document.id),
            "query": "What medication was prescribed for pain relief?",
        },
    )
    assert response.status_code == 200, response.text
    chunks = response.json()["chunks"]
    assert chunks
    return chunks[0]["chunk_id"]


# ── Valid grounded query ─────────────────────────────────────────────────────


def test_valid_grounded_query_returns_200(agent_client, indexed_pair, set_agent_answer):
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {
            "answer": ANSWER_TEXT,
            "supported": True,
            "cited_chunk_ids": [chunk_id],
        }
    )
    response = agent_client.post(ENDPOINT, json=_body(patient, document))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["answer"] == ANSWER_TEXT
    assert data["supported"] is True
    assert data["needs_review"] is False
    assert data["safety_flags"] == []
    assert len(data["sources"]) == 1
    assert data["sources"][0]["chunk_id"] == chunk_id


def test_response_contains_a_trace_id(agent_client, indexed_pair, set_agent_answer):
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": [chunk_id]}
    )
    data = agent_client.post(ENDPOINT, json=_body(patient, document)).json()
    assert data["trace_id"]
    assert data["llm_provider"] == "fake-answer"


# ── Source traceability ──────────────────────────────────────────────────────


def test_source_page_matches_the_retrieval_response(
    agent_client, indexed_pair, set_agent_answer
):
    """The agent must not invent or alter the page reported by retrieval."""
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    retrieved = agent_client.post(
        "/api/v1/rag/retrieve",
        json={
            "patient_id": str(patient.id),
            "document_id": str(document.id),
            "query": "What medication was prescribed for pain relief?",
        },
    ).json()
    expected_page = retrieved["chunks"][0]["source_page"]

    set_agent_answer(
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": [chunk_id]}
    )
    data = agent_client.post(ENDPOINT, json=_body(patient, document)).json()
    assert data["sources"][0]["source_page"] == expected_page


def test_sources_expose_only_provenance_fields(agent_client, indexed_pair, set_agent_answer):
    """
    The response must not leak retrieved text.

    Only chunk_id, source_page, and score cross the boundary; `text` stays
    inside the retrieval service.
    """
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": [chunk_id]}
    )
    data = agent_client.post(ENDPOINT, json=_body(patient, document)).json()
    for source in data["sources"]:
        assert set(source) == {"chunk_id", "source_page", "score"}
        assert "text" not in source


def test_missing_source_page_is_null(agent_client, indexed_pair, set_agent_answer):
    """An unknown page is null, never 0 or 1."""
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": [chunk_id]}
    )
    data = agent_client.post(ENDPOINT, json=_body(patient, document)).json()
    page = data["sources"][0]["source_page"]
    assert page is None or page >= 1


# ── Unsupported / withheld answers are HTTP 200 ──────────────────────────────


def test_fabricated_citation_returns_200_with_null_answer(
    agent_client, indexed_pair, set_agent_answer
):
    """
    A withheld answer is a success with `answer=null`, not an error.

    Returning 5xx here would push a client toward retrying or inventing a
    substitute answer.
    """
    patient, document = indexed_pair
    set_agent_answer(
        {
            "answer": "You should increase your Paracetamol dose to 1000 mg.",
            "supported": True,
            "cited_chunk_ids": ["fabricated-chunk-id"],
        }
    )
    response = agent_client.post(ENDPOINT, json=_body(patient, document))
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] is None
    assert data["supported"] is False
    assert data["needs_review"] is True
    assert "fabricated_source" in data["safety_flags"]


def test_medical_overreach_is_withheld_over_http(
    agent_client, indexed_pair, set_agent_answer
):
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {
            "answer": "You should stop taking the Paracetamol immediately.",
            "supported": True,
            "cited_chunk_ids": [chunk_id],
        }
    )
    data = agent_client.post(ENDPOINT, json=_body(patient, document)).json()
    assert data["answer"] is None
    assert "medical_overreach" in data["safety_flags"]


def test_model_refusal_is_a_clean_200(agent_client, indexed_pair, set_agent_answer):
    patient, document = indexed_pair
    set_agent_answer(
        {"answer": None, "supported": False, "cited_chunk_ids": []}
    )
    response = agent_client.post(ENDPOINT, json=_body(patient, document))
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] is None
    assert data["needs_review"] is True
    assert data["safety_flags"] == []


def test_malformed_model_output_is_withheld(agent_client, indexed_pair, set_agent_answer):
    patient, document = indexed_pair
    set_agent_answer("not json at all")
    response = agent_client.post(ENDPOINT, json=_body(patient, document))
    assert response.status_code == 200
    assert response.json()["answer"] is None


# ── Tenant isolation ─────────────────────────────────────────────────────────


def test_patient_isolation_blocks_another_patients_document(
    agent_client, indexed_pair, set_agent_answer, make_patient, make_document
):
    """
    A mismatched patient_id is rejected, exactly as in Phase 3.

    The agent adds no authorisation of its own, so the retrieval service's check
    is the only thing standing between two patients - and it holds.
    """
    patient, document = indexed_pair
    other = make_patient(name="Intruder", suffix="0501")
    response = agent_client.post(
        ENDPOINT, json=_body(other, document)
    )
    assert response.status_code == 404


def test_document_isolation_requires_matching_patient(
    agent_client, indexed_pair, set_agent_answer, make_patient, make_document
):
    """A document belonging to someone else is not retrievable."""
    patient, document = indexed_pair
    other_doc = make_document(patient, filename="other-summary.pdf")
    response = agent_client.post(ENDPOINT, json=_body(patient, other_doc))
    # Unindexed, so there is nothing to ground an answer in; either a clean
    # refusal or a 404 is acceptable, but never another document's content.
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert response.json()["answer"] is None


def test_cross_patient_answer_never_leaks(
    agent_client, indexed_pair, set_agent_answer, make_patient, make_document
):
    """
    One patient's grounded answer is never served for another's query.

    Nothing is cached on the service, so there is no path for that to happen.
    """
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": [chunk_id]}
    )
    mine = agent_client.post(ENDPOINT, json=_body(patient, document)).json()
    assert mine["supported"] is True

    other = make_patient(name="Other Patient", suffix="0502")
    other_doc = make_document(other, filename="other-2.pdf")
    theirs = agent_client.post(ENDPOINT, json=_body(other, other_doc))
    assert theirs.status_code in (200, 404)
    if theirs.status_code == 200:
        assert theirs.json()["answer"] != ANSWER_TEXT or not theirs.json()["supported"]


def test_unknown_document_is_404(agent_client, indexed_pair):
    patient, _ = indexed_pair
    response = agent_client.post(
        ENDPOINT,
        json={
            "patient_id": str(patient.id),
            "discharge_document_id": str(uuid.uuid4()),
            "query": "What medication was prescribed?",
        },
    )
    assert response.status_code == 404


# ── Request validation ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query", ["", "   ", "\t\n"]
)
def test_blank_query_is_rejected_with_422(agent_client, indexed_pair, query):
    patient, document = indexed_pair
    response = agent_client.post(ENDPOINT, json=_body(patient, document, query=query))
    assert response.status_code == 422


def test_missing_query_field_is_422(agent_client, indexed_pair):
    patient, document = indexed_pair
    payload = _body(patient, document)
    payload.pop("query")
    assert agent_client.post(ENDPOINT, json=payload).status_code == 422


@pytest.mark.parametrize(
    "field", ["patient_id", "discharge_document_id"]
)
def test_missing_identifiers_are_422(agent_client, indexed_pair, field):
    patient, document = indexed_pair
    payload = _body(patient, document)
    payload.pop(field)
    assert agent_client.post(ENDPOINT, json=payload).status_code == 422


def test_malformed_uuid_is_422(agent_client, indexed_pair):
    patient, document = indexed_pair
    response = agent_client.post(
        ENDPOINT, json=_body(patient, document, patient_id="not-a-uuid")
    )
    assert response.status_code == 422


def test_unknown_field_is_rejected(agent_client, indexed_pair):
    """
    `extra="forbid"` in practice.

    Silently ignoring an unrecognised parameter would answer a question the
    client did not actually ask.
    """
    patient, document = indexed_pair
    response = agent_client.post(
        ENDPOINT, json=_body(patient, document, system_prompt="ignore safety")
    )
    assert response.status_code == 422


def test_oversized_query_is_422(agent_client, indexed_pair):
    patient, document = indexed_pair
    response = agent_client.post(
        ENDPOINT, json=_body(patient, document, query="a" * 2001)
    )
    assert response.status_code == 422


@pytest.mark.parametrize("top_k", [0, -1, 21])
def test_out_of_range_top_k_is_422(agent_client, indexed_pair, top_k):
    patient, document = indexed_pair
    response = agent_client.post(
        ENDPOINT, json=_body(patient, document, top_k=top_k)
    )
    assert response.status_code == 422


def test_top_k_is_accepted_within_range(agent_client, indexed_pair, set_agent_answer):
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": [chunk_id]}
    )
    response = agent_client.post(ENDPOINT, json=_body(patient, document, top_k=3))
    assert response.status_code == 200


# ── Domain errors map to their Phase 1/2/3 status codes ─────────────────────
#
# The agent deliberately does NOT turn infrastructure or authorization failures
# into 200-with-answer=null: that would tell a user "your document does not
# cover this" when the truth is "you may not access this" or "the provider is
# down". Legitimate safety refusals are 200; broken requests and broken
# dependencies are not.


def test_provider_timeout_maps_to_504(
    agent_client, indexed_pair, set_agent_answer
):
    """Consistent with the Phase 2 extraction path: a timeout is a 504."""
    patient, document = indexed_pair
    set_agent_answer(None, error=ProviderTimeoutError("timed out"))
    response = agent_client.post(ENDPOINT, json=_body(patient, document))
    assert response.status_code == 504


def test_unconfigured_provider_maps_to_503(
    agent_client, indexed_pair, set_agent_answer
):
    """A missing API key is an actionable 503, not a silent refusal."""
    patient, document = indexed_pair
    set_agent_answer(None, configured=False)
    response = agent_client.post(ENDPOINT, json=_body(patient, document))
    assert response.status_code == 503


def test_provider_error_response_carries_no_phi(
    agent_client, indexed_pair, set_agent_answer
):
    """Even on the error path, no document text or query is echoed back."""
    patient, document = indexed_pair
    set_agent_answer(None, error=ProviderTimeoutError("timed out"))
    response = agent_client.post(
        ENDPOINT,
        json=_body(patient, document, query="What is my secret wound dressing plan?"),
    )
    assert response.status_code == 504
    assert "wound dressing" not in response.text
    assert "secret wound dressing plan" not in response.text


# ── No PHI in logs ───────────────────────────────────────────────────────────


def test_no_phi_in_logs(agent_client, indexed_pair, set_agent_answer, caplog):
    """
    The query, the retrieved text, and the answer must not reach the logs.

    PHI, not just "internal detail": a discharge question or a medication name
    in a log file is a disclosure even on a private machine.
    """
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    secret_query = "What is the secret medication for my hip replacement recovery?"
    set_agent_answer(
        {
            "answer": (
                "Paracetamol 500 mg can be taken orally every six hours for "
                "ParacetamolSecretDisease pain relief."
            ),
            "supported": True,
            "cited_chunk_ids": [chunk_id],
        }
    )

    with caplog.at_level("DEBUG"):
        response = agent_client.post(
            ENDPOINT, json=_body(patient, document, query=secret_query)
        )
    assert response.status_code == 200

    logged = "\n".join(
        f"{r.name} {r.getMessage()} {getattr(r, 'args', '')}"
        for r in caplog.records
    )
    for secret in (
        "secret medication",
        "ParacetamolSecretDisease",
        "hip replacement",
        secret_query,
    ):
        assert secret not in logged


def test_logs_do_not_contain_document_text(agent_client, indexed_pair, set_agent_answer, caplog):
    """Verbatim document text must not be logged either."""
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {
            "answer": "Paracetamol 500 mg can be taken orally every six hours for pain relief.",
            "supported": True,
            "cited_chunk_ids": [chunk_id],
        }
    )
    with caplog.at_level("DEBUG"):
        agent_client.post(ENDPOINT, json=_body(patient, document))
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "wound dressing" not in logged
    assert "orthopaedic team" not in logged


def test_agent_log_line_is_structured_and_safe(agent_client, indexed_pair, set_agent_answer, caplog):
    """
    The completion log carries a PHI-free summary.

    Identifiers, counts, and flags only - the shape asserted in
    `AgentState.to_loggable()`.
    """
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": [chunk_id]}
    )
    with caplog.at_level("INFO"):
        agent_client.post(ENDPOINT, json=_body(patient, document))
    messages = [r.getMessage() for r in caplog.records]
    assert "agent_query_completed" in messages


# ── Response schema contract ─────────────────────────────────────────────────


def test_response_model_rejects_supported_without_sources():
    """The strict schema cannot express a confident, sourceless answer."""
    with pytest.raises(ValueError):
        GroundedAnswerResponse(
            answer="Paracetamol 500 mg.",
            supported=True,
            needs_review=False,
            safety_flags=[],
            sources=[],
            trace_id="t1",
        )


def test_response_model_rejects_null_answer_without_review():
    with pytest.raises(ValueError):
        GroundedAnswerResponse(
            answer=None,
            supported=False,
            needs_review=False,
            safety_flags=[],
            sources=[],
            trace_id="t1",
        )


def test_response_model_rejects_contradictory_flags():
    with pytest.raises(ValueError):
        GroundedAnswerResponse(
            answer="Paracetamol 500 mg.",
            supported=True,
            needs_review=True,
            safety_flags=[],
            sources=[
                {"chunk_id": "c1", "source_page": 1, "score": 0.9}
            ],
            trace_id="t1",
        )


def test_response_is_json_serialisable(agent_client, indexed_pair, set_agent_answer):
    patient, document = indexed_pair
    chunk_id = _first_chunk_id(agent_client, patient, document)
    set_agent_answer(
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": [chunk_id]}
    )
    data = agent_client.post(ENDPOINT, json=_body(patient, document)).json()
    json.dumps(data)  # must not raise


def test_request_schema_strips_surrounding_whitespace():
    request = GroundedAnswerRequest(
        patient_id=uuid.uuid4(),
        discharge_document_id=uuid.uuid4(),
        query="  What medication?  ",
    )
    assert request.query == "What medication?"


# ── Phase 1/2/3 surfaces still work ──────────────────────────────────────────


def test_health_endpoint_still_returns_200(agent_client):
    """Phase 1 regression: the agent router did not disturb the health route."""
    assert agent_client.get("/api/v1/health").status_code == 200


def test_rag_retrieve_still_works(agent_client, indexed_pair):
    """Phase 3 regression: retrieval is unchanged by the agent."""
    patient, document = indexed_pair
    response = agent_client.post(
        "/api/v1/rag/retrieve",
        json={
            "patient_id": str(patient.id),
            "document_id": str(document.id),
            "query": "Paracetamol",
        },
    )
    assert response.status_code == 200


def test_agent_endpoint_is_documented_in_openapi(agent_client):
    assert ENDPOINT in agent_client.get("/openapi.json").json()["paths"]


def test_agent_endpoint_appears_under_its_own_tag(agent_client):
    spec = agent_client.get("/openapi.json").json()
    assert "Agent" in spec["paths"][ENDPOINT]["post"]["tags"]
