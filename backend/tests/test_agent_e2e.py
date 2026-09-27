"""
CareLoop AI - Phase 4 End-to-End Graph Tests

Drives the compiled `AgentService` through the real Phase 3 retrieval service
(temp Chroma directory, `FakeEmbeddingProvider`) and a mocked LLM.  These are
the tests that prove the pieces work together: grounded success, refusal,
withheld answers, provider faults, retrieval faults, and the safe fallback.

NO NETWORK. The provider is always a `FakeAnswerProvider`.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import ProviderTimeoutError
from tests.conftest import FakeAnswerProvider

# A paraphrase of the indexed sample text, so the lexical grounding guard is
# satisfied by real overlap rather than by a verbatim copy.
ANSWER_TEXT = (
    "Paracetamol 500 mg can be taken orally every six hours as required for "
    "pain relief, and it should be continued for five days."
)


@pytest.fixture
def first_chunk_id(agent_fixtures):
    """The real chunk id produced by retrieval for a medication question."""
    result = agent_fixtures["retrieval"].retrieve(
        patient_id=agent_fixtures["patient"].id,
        document_id=agent_fixtures["document"].id,
        query="What medication was prescribed for pain relief?",
    )
    assert result.chunks, "fixture must retrieve at least one chunk"
    return result.chunks[0].chunk_id


def _query(agent_fixtures, query="What medication was prescribed for pain relief?"):
    return agent_fixtures["service"].query(
        patient_id=agent_fixtures["patient"].id,
        discharge_document_id=agent_fixtures["document"].id,
        user_query=query,
    )


def _set_payload(agent_fixtures, payload, **kwargs):
    agent_fixtures["provider"]._payload = payload
    for key, value in kwargs.items():
        setattr(agent_fixtures["provider"], f"_{key}", value)


# ── Valid grounded query ─────────────────────────────────────────────────────


def test_valid_grounded_query_returns_a_supported_answer(
    agent_fixtures, first_chunk_id
):
    """
    The happy path: real retrieval, real chunk ids, mocked model, safe answer.
    """
    _set_payload(
        agent_fixtures,
        {
            "answer": ANSWER_TEXT,
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    result = _query(agent_fixtures)

    assert result.answer == ANSWER_TEXT
    assert result.supported is True
    assert result.needs_review is False
    assert result.safety_flags == []
    assert len(result.sources) == 1
    assert result.sources[0].chunk_id == first_chunk_id


def test_grounded_answer_carries_trace_and_provider(
    agent_fixtures, first_chunk_id
):
    """Observability fields are populated without leaking content."""
    _set_payload(
        agent_fixtures,
        {
            "answer": ANSWER_TEXT,
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    result = _query(agent_fixtures)
    assert result.trace_id
    assert result.llm_provider == "fake-answer"
    assert result.route == "safe"
    assert "generate_grounded_response" in result.node_trace
    assert "validate_safety" in result.node_trace


# ── Source traceability ──────────────────────────────────────────────────────


def test_source_page_is_traced_from_the_indexed_chunk(agent_fixtures, first_chunk_id):
    """A page number in the response must exist in the real indexed metadata."""
    _set_payload(
        agent_fixtures,
        {
            "answer": ANSWER_TEXT,
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    result = _query(agent_fixtures)
    expected = agent_fixtures["retrieval"].retrieve(
        patient_id=agent_fixtures["patient"].id,
        document_id=agent_fixtures["document"].id,
        query="What medication was prescribed for pain relief?",
    ).chunks[0].source_page
    assert result.sources[0].source_page == expected


def test_missing_source_page_is_reported_as_null(
    agent_fixtures, first_chunk_id
):
    """
    An absent page marker surfaces as null, never as page 1.

    The document is page-marked, so a null here can only arise from a chunk
    without one; either way the contract is that unknown stays unknown.
    """
    from app.agent.state import GroundedSource

    _set_payload(
        agent_fixtures,
        {
            "answer": ANSWER_TEXT,
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    result = _query(agent_fixtures)
    for source in result.sources:
        assert isinstance(source, GroundedSource)
        assert source.source_page is None or source.source_page > 0


# ── Unsupported query / model refusal ────────────────────────────────────────


def test_model_refusal_is_returned_as_a_clean_no_answer(agent_fixtures):
    """
    An honest "not in the document" is a success, not an error.

    No flag, no fallback: the model declining is a legitimate outcome.
    """
    _set_payload(
        agent_fixtures,
        {
            "answer": None,
            "supported": False,
            "cited_chunk_ids": [],
            "model_declined_reason": "passages do not address the question",
        },
    )
    result = _query(agent_fixtures)
    assert result.answer is None
    assert result.supported is False
    assert result.needs_review is True
    assert result.safety_flags == []


def test_fabricated_citation_is_withheld_end_to_end(
    agent_fixtures, first_chunk_id
):
    """
    A confident answer citing an invented chunk never reaches the caller.

    This is the headline safety property: plausible text plus a fake citation
    still produces `answer=null`.
    """
    _set_payload(
        agent_fixtures,
        {
            "answer": "You should increase your Paracetamol dose to 1000 mg.",
            "supported": True,
            "cited_chunk_ids": ["chunk-that-does-not-exist"],
        },
    )
    result = _query(agent_fixtures)
    assert result.answer is None
    assert result.supported is False
    assert result.needs_review is True
    assert "fabricated_source" in result.safety_flags
    assert "safe_fallback" in result.node_trace


def test_medical_overreach_is_withheld_end_to_end(agent_fixtures, first_chunk_id):
    """A real citation does not rescue overreaching guidance."""
    _set_payload(
        agent_fixtures,
        {
            "answer": "You should take Paracetamol 1000 mg every four hours.",
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    result = _query(agent_fixtures)
    assert result.answer is None
    assert "medical_overreach" in result.safety_flags


def test_unrelated_answer_is_withheld_by_grounding_guard(
    agent_fixtures, first_chunk_id
):
    """Fluent text unrelated to the sources fails the lexical guard."""
    _set_payload(
        agent_fixtures,
        {
            "answer": (
                "Undertake aquatic aerobics thrice weekly and supplement with a "
                "probiotic containing Lactobacillus for gastrointestinal "
                "restoration."
            ),
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    result = _query(agent_fixtures)
    assert result.answer is None
    assert "grounding_not_established" in result.safety_flags


# ── No retrieval results ─────────────────────────────────────────────────────


def test_no_retrieval_results_yields_no_context(agent_fixtures, monkeypatch):
    """
    Zero matching chunks produces no answer and no model call.

    The empty result is stubbed rather than produced with an "unrelated" query,
    because `FakeEmbeddingProvider` scores lexically, so an off-topic question
    can still clear the relevance floor. Stubbing isolates the routing decision
    this test is actually about; the real floor is already covered by Phase 3's
    own retrieval tests.
    """
    from app.rag.retrieval import RetrievalResult

    def _no_hits(**kwargs):
        return RetrievalResult(
            document_id=kwargs["document_id"],
            patient_id=kwargs["patient_id"],
            query_length=len(kwargs["query"]),
            indexed_chunks=2,
            min_score=0.1,
            chunks=(),
        )

    monkeypatch.setattr(agent_fixtures["retrieval"], "retrieve", _no_hits)
    result = _query(agent_fixtures)

    assert result.answer is None
    assert result.supported is False
    assert result.needs_review is True
    assert "no_retrieval_match" in result.safety_flags
    assert agent_fixtures["provider"].calls == []
    assert "generate_grounded_response" not in result.node_trace


# ── Empty / invalid query ────────────────────────────────────────────────────


def test_whitespace_query_is_rejected_without_spending_a_call(agent_fixtures):
    """Blank input stops before retrieval and before the model."""
    result = agent_fixtures["service"].query(
        patient_id=agent_fixtures["patient"].id,
        discharge_document_id=agent_fixtures["document"].id,
        user_query="   ",
    )
    assert result.answer is None
    assert result.needs_review is True
    assert "insufficient_evidence" in result.safety_flags
    assert agent_fixtures["provider"].calls == []


# ── Mocked LLM failure ───────────────────────────────────────────────────────
#
# Provider faults PROPAGATE rather than becoming `answer=null`, so a client can
# tell "the provider is down" from "the document does not cover this". Turning
# an outage into a settled-looking no-answer would be actively misleading.


def test_provider_failure_propagates_as_a_domain_error(agent_fixtures):
    """A provider fault surfaces as a 504-mapped domain error, not a refusal."""
    from app.core.exceptions import ProviderTimeoutError

    _set_payload(agent_fixtures, None, error=ProviderTimeoutError("timed out"))
    with pytest.raises(ProviderTimeoutError):
        _query(agent_fixtures)


def test_unconfigured_provider_propagates_an_actionable_error(agent_fixtures):
    """
    A missing API key is an actionable 503, never a silent refusal.

    Silently refusing would look identical to "the document does not cover
    this", hiding a configuration problem from the operator.
    """
    from app.core.exceptions import ProviderNotConfiguredError

    _set_payload(agent_fixtures, None, error=None, configured=False)
    with pytest.raises(ProviderNotConfiguredError):
        _query(agent_fixtures)


def test_malformed_model_output_yields_flagged_no_answer(agent_fixtures):
    """
    A well-formed response carrying unusable content IS a legitimate refusal.

    This is different from a provider fault: the call succeeded, the model
    simply produced nothing usable, which is a safety outcome rather than an
    infrastructure error.
    """
    _set_payload(agent_fixtures, "this is not json at all")
    result = _query(agent_fixtures)
    assert result.answer is None
    assert result.needs_review is True


# ── Chroma / retrieval failure ───────────────────────────────────────────────


def test_chroma_failure_propagates_as_a_domain_error(agent_fixtures, monkeypatch):
    """
    A vector-store outage is a 503-mapped domain error.

    The caller can distinguish "the store is down" from "nothing matched",
    which is the difference between a retry and a dead end.
    """
    from app.core.exceptions import VectorStoreError

    def _boom(**kwargs):
        raise VectorStoreError("Vector store unavailable.")

    monkeypatch.setattr(agent_fixtures["retrieval"], "retrieve", _boom)
    with pytest.raises(VectorStoreError):
        _query(agent_fixtures)


def test_ownership_violation_propagates(agent_fixtures, monkeypatch):
    """
    An authorization failure is never disguised as a refusal.

    Returning 200-with-no-answer for a cross-patient request would hide an
    isolation bug behind a benign-looking response.
    """
    from app.core.exceptions import RetrievalForbiddenError

    def _forbidden(**kwargs):
        raise RetrievalForbiddenError("Document does not belong to patient.")

    monkeypatch.setattr(agent_fixtures["retrieval"], "retrieve", _forbidden)
    with pytest.raises(RetrievalForbiddenError):
        _query(agent_fixtures)


def test_unexpected_retrieval_exception_is_contained(agent_fixtures, monkeypatch):
    """
    An unforeseen fault is contained and still fails closed.

    No stack trace reaches the client, and nothing is asserted.
    """

    def _boom(**kwargs):
        raise RuntimeError("unexpected chroma internal failure")

    monkeypatch.setattr(agent_fixtures["retrieval"], "retrieve", _boom)
    result = _query(agent_fixtures)
    assert result.answer is None
    assert result.supported is False
    assert result.needs_review is True
    assert "retrieval_unavailable" in result.errors


# ── Safe fallback is the single terminal path ────────────────────────────────


def test_every_withheld_answer_reaches_the_safe_fallback(agent_fixtures, first_chunk_id):
    """
    All failure shapes converge on one node.

    Consistency here matters: a caller can rely on `safe_fallback` meaning "an
    answer existed but was not safe to show".
    """
    for payload in (
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": ["fake"]},
        {"answer": "You should stop taking it.", "supported": True, "cited_chunk_ids": [first_chunk_id]},
        {"answer": ANSWER_TEXT, "supported": True, "cited_chunk_ids": []},
    ):
        _set_payload(agent_fixtures, payload)
        result = _query(agent_fixtures)
        assert result.answer is None
        assert "safe_fallback" in result.node_trace
        assert result.route == "unsafe"


def test_safe_fallback_returns_no_clinical_text(agent_fixtures, first_chunk_id):
    """The fallback emits identifiers and flags only."""
    _set_payload(
        agent_fixtures,
        {
            "answer": "You should stop taking the Paracetamol immediately.",
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    result = _query(agent_fixtures)
    assert result.answer is None
    assert "Paracetamol" not in repr(result.to_loggable())


# ── Statelessness ────────────────────────────────────────────────────────────


def test_repeated_queries_are_independent(agent_fixtures, first_chunk_id):
    """
    Two runs in one session must not influence each other.

    Phase 4 keeps no conversation memory, so the second run behaves exactly
    like the first.
    """
    _set_payload(
        agent_fixtures,
        {
            "answer": ANSWER_TEXT,
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    first = _query(agent_fixtures)
    second = _query(agent_fixtures)
    assert first.answer == second.answer
    assert first.trace_id != second.trace_id


def test_different_patients_do_not_share_answers(agent_fixtures, first_chunk_id, make_patient, make_document):
    """
    A second patient's document produces its own independent result.

    Nothing is cached on the service, so there is no path for one patient's
    answer to be served for another.
    """
    _set_payload(
        agent_fixtures,
        {
            "answer": ANSWER_TEXT,
            "supported": True,
            "cited_chunk_ids": [first_chunk_id],
        },
    )
    first = _query(agent_fixtures)
    other_patient = make_patient(name="Second Patient", suffix="0401")
    other_document = make_document(other_patient, filename="other.pdf")
    second = agent_fixtures["service"].query(
        patient_id=other_patient.id,
        discharge_document_id=other_document.id,
        user_query="What medication was prescribed for pain relief?",
    )
    assert first.trace_id != second.trace_id
