"""
CareLoop AI - Phase 4 Generation Tests

Covers the LLM contract: mocked success, provider failure, unconfigured
provider, malformed output, prompt content, and - most importantly - that
source provenance is resolved from real chunk metadata rather than accepted
from the model.

NO NETWORK. Every test here uses a `FakeAnswerProvider`; the real Groq and
Gemini clients are never constructed and no API key is read.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.agent.generation import (
    GROUNDED_SCHEMA_NAME,
    GroundedAnswerDraft,
    GroundedResponseGenerator,
)
from app.core.exceptions import (
    ProviderNotConfiguredError,
    ProviderTimeoutError,
)
from app.rag.retrieval import RetrievedChunk
from tests.conftest import FakeAnswerProvider

CHUNK_TEXT = (
    "Paracetamol 500 mg to be taken orally every six hours as required for "
    "pain relief. Continue for five days."
)


def _chunk(chunk_id="c1", text=CHUNK_TEXT, page=1) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id, text=text, source_page=page, score=0.87, distance=0.13
    )


def _generator(provider, settings=None) -> GroundedResponseGenerator:
    return GroundedResponseGenerator(settings=settings, llm_provider=provider)


# ── Mocked success ───────────────────────────────────────────────────────────


def test_mocked_provider_success_yields_draft_and_sources():
    provider = FakeAnswerProvider(
        payload={
            "answer": CHUNK_TEXT,
            "supported": True,
            "cited_chunk_ids": ["c1"],
        }
    )
    doc = uuid.uuid4()
    draft, sources, name, model = _generator(provider).generate(
        user_query="What is my pain relief?",
        chunks=[_chunk()],
        document_id=doc,
    )
    assert draft.supported is True
    assert draft.answer == CHUNK_TEXT
    assert len(sources) == 1
    assert name == "fake-answer"
    assert model


def test_sources_come_from_real_chunk_metadata():
    """Every source field is copied from the retrieved chunk."""
    provider = FakeAnswerProvider(
        payload={
            "answer": CHUNK_TEXT,
            "supported": True,
            "cited_chunk_ids": ["real-id"],
        }
    )
    doc = uuid.uuid4()
    _, sources, _, _ = _generator(provider).generate(
        user_query="q", chunks=[_chunk(page=7, chunk_id="real-id")], document_id=doc
    )
    source = sources[0]
    assert source.chunk_id == "real-id"      # from the chunk
    assert source.source_page == 7            # from the chunk
    assert source.score == pytest.approx(0.87)  # from the chunk
    assert source.discharge_document_id == doc  # from the request


# ── The model cannot assert provenance ───────────────────────────────────────


def test_fabricated_citation_yields_no_source():
    """
    An invented chunk id resolves to nothing, so no source is emitted.

    Detection is the safety node's job; here the guarantee is narrower and
    stronger: the fabricated id cannot become provenance.
    """
    provider = FakeAnswerProvider(
        payload={
            "answer": "You should take 1000 mg.",
            "supported": True,
            "cited_chunk_ids": ["totally-made-up"],
        }
    )
    _, sources, _, _ = _generator(provider).generate(
        user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
    )
    assert sources == []


def test_model_supplied_page_number_is_never_read():
    """
    Even if the model volunteers a page, provenance comes from the chunk.

    The model returning extra keys is the realistic threat here: it cannot
    reach `GroundedSource` because the draft model forbids extra fields and the
    source is constructed from the chunk alone.
    """
    provider = FakeAnswerProvider(
        payload={
            "answer": CHUNK_TEXT,
            "supported": True,
            "cited_chunk_ids": ["c1"],
            "source_page": 999,
            "discharge_document_id": str(uuid.uuid4()),
        }
    )
    _, sources, _, _ = _generator(provider).generate(
        user_query="q", chunks=[_chunk(page=2)], document_id=uuid.uuid4()
    )
    assert sources[0].source_page == 2  # the chunk's page, not 999


def test_missing_source_page_is_preserved_as_null():
    """
    An absent page marker stays null - never defaulted to 1 or estimated.

    A wrong page number in a clinical document is worse than an honest unknown.
    """
    provider = FakeAnswerProvider(
        payload={"answer": CHUNK_TEXT, "supported": True, "cited_chunk_ids": ["c1"]}
    )
    _, sources, _, _ = _generator(provider).generate(
        user_query="q", chunks=[_chunk(page=None)], document_id=uuid.uuid4()
    )
    assert sources[0].source_page is None


def test_multiple_citations_resolve_to_multiple_sources():
    provider = FakeAnswerProvider(
        payload={
            "answer": CHUNK_TEXT,
            "supported": True,
            "cited_chunk_ids": ["c1", "c2"],
        }
    )
    _, sources, _, _ = _generator(provider).generate(
        user_query="q",
        chunks=[_chunk("c1", page=1), _chunk("c2", page=2)],
        document_id=uuid.uuid4(),
    )
    assert {s.chunk_id for s in sources} == {"c1", "c2"}
    assert {s.source_page for s in sources} == {1, 2}


def test_duplicate_citations_are_deduplicated():
    provider = FakeAnswerProvider(
        payload={
            "answer": CHUNK_TEXT,
            "supported": True,
            "cited_chunk_ids": ["c1", "c1", "c1"],
        }
    )
    _, sources, _, _ = _generator(provider).generate(
        user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
    )
    assert len(sources) == 1


# ── Provider failure ─────────────────────────────────────────────────────────


def test_provider_error_propagates_as_domain_exception():
    """A provider fault stays a domain error so the API can map it to 502."""
    provider = FakeAnswerProvider(error=ProviderTimeoutError("timed out"))
    with pytest.raises(ProviderTimeoutError):
        _generator(provider).generate(
            user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
        )


def test_unconfigured_provider_raises_actionable_error():
    """
    A missing key is reported, not silently turned into a refusal.

    Silently refusing would look identical to "the document does not cover
    this", hiding a configuration problem from the operator.
    """
    provider = FakeAnswerProvider(configured=False)
    with pytest.raises(ProviderNotConfiguredError) as exc:
        _generator(provider).generate(
            user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
        )
    assert "not configured" in str(exc.value)


def test_unconfigured_provider_makes_no_call():
    """The provider must not be invoked without configuration."""
    provider = FakeAnswerProvider(configured=False)
    with pytest.raises(ProviderNotConfiguredError):
        _generator(provider).generate(
            user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
        )
    assert provider.calls == []


# ── Malformed output ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "a bare string",
        123,
        ["a", "list"],
        {"supported": "not a boolean at all"},
        {"answer": {"nested": "object"}},
    ],
)
def test_malformed_payload_yields_no_draft(payload):
    """
    Unusable output becomes no draft, so the caller must take the safe path.

    Nothing downstream can mistake a malformed payload for content.
    """
    provider = FakeAnswerProvider(payload=payload)
    draft, _, _, _ = _generator(provider).generate(
        user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
    )
    assert draft is None


def test_unknown_extra_keys_are_ignored_not_fatal():
    """
    Extra keys are tolerated so a chatty model still produces a usable draft.

    They are dropped, not trusted - which is why the invented `source_page` in
    `test_model_supplied_page_number_is_never_read` cannot reach provenance.
    """
    provider = FakeAnswerProvider(
        payload={
            "answer": CHUNK_TEXT,
            "supported": True,
            "cited_chunk_ids": ["c1"],
            "reasoning": "internal chain of thought that must be discarded",
        }
    )
    draft, _, _, _ = _generator(provider).generate(
        user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
    )
    assert draft is not None
    assert not hasattr(draft, "reasoning")


def test_draft_schema_is_serialisable():
    """The schema handed to the provider must be valid JSON."""
    schema = GroundedAnswerDraft.json_schema()
    json.dumps(schema)  # must not raise
    assert "properties" in schema
    assert "cited_chunk_ids" in schema["properties"]


# ── Prompt content ───────────────────────────────────────────────────────────


def test_prompt_contains_the_real_chunk_id():
    """
    The model is shown the ids it may cite, so citing one is legitimate.
    """
    provider = FakeAnswerProvider(
        payload={"answer": CHUNK_TEXT, "supported": True, "cited_chunk_ids": ["c1"]}
    )
    _generator(provider).generate(
        user_query="What is my pain relief?",
        chunks=[_chunk(chunk_id="chunk-abc")],
        document_id=uuid.uuid4(),
    )
    call = provider.calls[0]
    assert "chunk-abc" in call["user_prompt"]
    assert "What is my pain relief?" in call["user_prompt"]
    assert call["schema_name"] == GROUNDED_SCHEMA_NAME


def test_prompt_forbids_inventing_pages():
    """The prompt states the model must never state a page number."""
    provider = FakeAnswerProvider(
        payload={"answer": None, "supported": False, "cited_chunk_ids": []}
    )
    _generator(provider).generate(
        user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
    )
    system = provider.calls[0]["system_prompt"]
    assert "page number" in system.lower()


def test_prompt_forbids_diagnosis_and_dose_changes():
    """The medical prohibitions are present in the prompt itself."""
    provider = FakeAnswerProvider(
        payload={"answer": None, "supported": False, "cited_chunk_ids": []}
    )
    _generator(provider).generate(
        user_query="q", chunks=[_chunk()], document_id=uuid.uuid4()
    )
    system = provider.calls[0]["system_prompt"].lower()
    for prohibition in ("diagnose", "dose", "emergency"):
        assert prohibition in system


def test_prompt_budget_is_enforced():
    """A large corpus is truncated to the configured prompt budget."""
    settings = type("S", (), {"agent_max_prompt_chars": 200})()
    provider = FakeAnswerProvider(
        payload={"answer": None, "supported": False, "cited_chunk_ids": []}
    )
    _generator(provider, settings=settings).generate(
        user_query="q",
        chunks=[_chunk("c1", text="x" * 5000)],
        document_id=uuid.uuid4(),
    )
    assert len(provider.calls[0]["user_prompt"]) < 1000


def test_no_chunks_renders_the_empty_passage_marker():
    provider = FakeAnswerProvider(
        payload={"answer": None, "supported": False, "cited_chunk_ids": []}
    )
    _generator(provider).generate(
        user_query="q", chunks=[], document_id=uuid.uuid4()
    )
    assert "no passages available" in provider.calls[0]["user_prompt"]


def test_chunk_without_page_renders_as_unknown():
    """The model is told 'unknown', so it cannot infer a page."""
    provider = FakeAnswerProvider(
        payload={"answer": None, "supported": False, "cited_chunk_ids": []}
    )
    _generator(provider).generate(
        user_query="q", chunks=[_chunk(page=None)], document_id=uuid.uuid4()
    )
    assert "page unknown" in provider.calls[0]["user_prompt"]


# ── Provider resolution ──────────────────────────────────────────────────────


def test_generator_resolves_provider_lazily():
    """
    Constructing the generator with no provider must not fail at import time.

    Same discipline as Phase 2's `ExtractionService`: a missing key surfaces
    when an answer is needed, not when the module loads.
    """
    generator = GroundedResponseGenerator(llm_provider=None)
    assert generator is not None
