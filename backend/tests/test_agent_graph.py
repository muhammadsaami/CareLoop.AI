"""
CareLoop AI - Phase 4 Agent Graph Tests

Covers graph construction, deterministic routing, request validation, and the
acyclic/no-loop guarantee.  No network, no LLM, no Chroma.
"""
from __future__ import annotations

import uuid

import pytest

from app.agent.generation import GroundedResponseGenerator
from app.agent.graph import (
    ALL_NODES,
    NODE_GENERATE,
    NODE_RETRIEVE,
    NODE_SAFE_FALLBACK,
    NODE_VALIDATE_REQUEST,
    NODE_VALIDATE_SAFETY,
    build_agent_graph,
    route_after_retrieval,
    route_after_safety,
    route_after_validation,
)
from app.agent.safety import AgentSafetyValidator
from app.agent.state import AgentState, GroundedAnswer, GroundedSource, Route
from app.rag.retrieval import RetrievedChunk


class NeverCalledRetrieval:
    """Retrieval double that fails loudly if a test reaches it."""

    def retrieve(self, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("retrieval must not be called in this test")


def _chunk(chunk_id="c1", text="Paracetamol 500 mg every six hours.", page=1):
    return RetrievedChunk(
        chunk_id=chunk_id, text=text, source_page=page, score=0.9, distance=0.1
    )


def _state(**overrides) -> AgentState:
    base = dict(
        patient_id=uuid.uuid4(),
        discharge_document_id=uuid.uuid4(),
        user_query="What medication was prescribed?",
    )
    base.update(overrides)
    return AgentState(**base)


def _source(chunk_id="c1", document_id=None, page=1) -> GroundedSource:
    return GroundedSource(
        discharge_document_id=document_id or uuid.uuid4(),
        chunk_id=chunk_id,
        source_page=page,
        score=0.9,
    )


def _build():
    return build_agent_graph(
        retrieval=NeverCalledRetrieval(),
        generator=GroundedResponseGenerator(llm_provider=None),
        validator=AgentSafetyValidator(),
    )


# ── Graph construction ───────────────────────────────────────────────────────


def test_graph_declares_every_required_node():
    """All five agreed nodes exist, named exactly as specified."""
    assert set(ALL_NODES) == {
        "validate_request",
        "retrieve_grounded_context",
        "generate_grounded_response",
        "validate_safety",
        "safe_fallback",
    }
    assert NODE_VALIDATE_REQUEST == "validate_request"
    assert NODE_RETRIEVE == "retrieve_grounded_context"
    assert NODE_GENERATE == "generate_grounded_response"
    assert NODE_VALIDATE_SAFETY == "validate_safety"
    assert NODE_SAFE_FALLBACK == "safe_fallback"


def test_graph_compiles():
    assert _build() is not None


def test_compiled_graph_contains_all_nodes():
    nodes = set(_build().get_graph().nodes)
    assert set(ALL_NODES).issubset(nodes)


def test_graph_has_no_cycles():
    """
    No node may reach itself, directly or transitively.

    This is the mechanical check behind "no autonomous agent loop": a cycle
    would let the model keep spending tokens until it felt satisfied, which is
    exactly the unbounded behaviour Phase 4 excludes.
    """
    graph = _build().get_graph()

    adjacency: dict[str, list[str]] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)

    visited: set[str] = set()
    active: set[str] = set()

    def _walk(node: str) -> None:
        if node in active:
            raise AssertionError(f"cycle detected through {node}")
        if node in visited:
            return
        active.add(node)
        for nxt in adjacency.get(node, []):
            _walk(nxt)
        active.discard(node)
        visited.add(node)

    for node in adjacency:
        _walk(node)


def test_no_edge_returns_to_start():
    """Nothing routes back to the beginning of the graph."""
    graph = _build().get_graph()
    assert all(edge.target != "__start__" for edge in graph.edges)


def test_no_agent_loop_node_or_edge():
    """
    Guard against a future 'continue'/'reflect'/'retry' node sneaking in.

    A loop would be a new node or a new edge back up the graph, so asserting
    the exact edge set makes any addition a deliberate, visible change rather
    than an accident.
    """
    graph = _build().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert edges == {
        ("__start__", NODE_VALIDATE_REQUEST),
        (NODE_VALIDATE_REQUEST, NODE_RETRIEVE),
        (NODE_VALIDATE_REQUEST, "__end__"),
        (NODE_RETRIEVE, NODE_GENERATE),
        (NODE_RETRIEVE, "__end__"),
        # Generation failure bypasses safety: there is no draft to validate.
        (NODE_GENERATE, NODE_VALIDATE_SAFETY),
        (NODE_GENERATE, NODE_SAFE_FALLBACK),
        (NODE_VALIDATE_SAFETY, "__end__"),
        (NODE_VALIDATE_SAFETY, NODE_SAFE_FALLBACK),
        (NODE_SAFE_FALLBACK, "__end__"),
    }


def test_generate_node_is_always_followed_by_safety_validation():
    """
    A generated draft cannot reach END without passing safety validation.

    The only other exit is the fallback, which emits no answer - so there is no
    path from a draft to a user that skips the check.
    """
    graph = _build().get_graph()
    generate_out = {
        edge.target for edge in graph.edges if edge.source == NODE_GENERATE
    }
    assert generate_out == {NODE_VALIDATE_SAFETY, NODE_SAFE_FALLBACK}


def test_route_after_generation_skips_validation_without_a_draft():
    """
    No draft means no validation step.

    Running `validate_safety` over nothing would replace the true diagnosis
    (`provider_unavailable`) with a misleading one (`invalid_model_output`).
    """
    from app.agent.graph import route_after_generation

    assert route_after_generation(_state()) is Route.UNSAFE
    assert (
        route_after_generation(
            _state(
                grounded_answer=GroundedAnswer(
                    answer="Paracetamol 500 mg.", supported=True, cited_chunk_ids=["c1"]
                )
            )
        )
        is Route.VALIDATED
    )


# ── Deterministic routing ────────────────────────────────────────────────────


def test_route_after_validation_accepts_non_empty_query():
    assert route_after_validation(_state(user_query="What now?")) is Route.VALIDATED


@pytest.mark.parametrize("blank", [" ", "   ", "\t\n  "])
def test_route_after_validation_rejects_blank_query(blank):
    """Blank input terminates instead of spending a retrieval or a call."""
    assert route_after_validation(_state(user_query=blank)) is Route.NO_CONTEXT


def test_state_rejects_a_fully_empty_query():
    """
    A completely empty query is rejected by the typed state itself.

    Whitespace-only queries pass `min_length` and are caught by the router
    instead, so both layers are covered: the model catches "" and the graph
    catches " ".
    """
    with pytest.raises(ValueError, match="at least 1 character"):
        _state(user_query="")


def test_route_after_retrieval_proceeds_only_with_sources():
    assert route_after_retrieval(_state(sources=[_source()])) is Route.VALIDATED
    assert route_after_retrieval(_state(sources=[])) is Route.NO_CONTEXT


def test_route_after_safety_emits_only_a_reviewed_answer():
    """A withheld answer routes to the fallback even if a string is present."""
    answer = "Paracetamol 500 mg every six hours."

    assert (
        route_after_safety(
            _state(answer=answer, supported=True, needs_review=False, sources=[_source()])
        )
        is Route.SAFE
    )
    assert (
        route_after_safety(
            _state(answer=answer, supported=False, needs_review=True, sources=[_source()])
        )
        is Route.UNSAFE
    )
    assert (
        route_after_safety(
            _state(answer=None, supported=False, needs_review=True, sources=[])
        )
        is Route.UNSAFE
    )


def test_route_after_safety_ignores_the_models_own_supported_claim():
    """
    The model's belief about itself must not influence control flow.

    `supported` is the field the LLM populated. If routing consulted it, a
    confident hallucination would be published.
    """
    state = _state(
        answer="Paracetamol 500 mg every six hours.",
        supported=False,  # model's own claim
        needs_review=True,  # safety overrode it
        sources=[_source()],
    )
    assert route_after_safety(state) is Route.UNSAFE


# ── Typed state invariants ───────────────────────────────────────────────────


def test_state_is_a_pydantic_model():
    """The graph state is typed, not a dict."""
    assert issubclass(AgentState, __import__("pydantic").BaseModel)


def test_state_rejects_supported_without_answer():
    with pytest.raises(ValueError, match="non-empty answer"):
        _state(supported=True, sources=[_source()])


def test_state_rejects_supported_without_sources():
    with pytest.raises(ValueError, match="at least one source"):
        _state(answer="Paracetamol 500 mg.", supported=True, sources=[])


def test_state_rejects_null_answer_without_needs_review():
    """
    A null answer cannot be marked `supported`.

    The stronger rule - a *completed* run with a null answer must set
    `needs_review` - is enforced in `AgentService._assert_fail_closed`, which
    is the only place that knows the run finished.  See
    `test_agent_service.py` for that boundary.
    """
    with pytest.raises(ValueError, match="supported=True requires"):
        _state(answer=None, supported=True, sources=[_source()])


def test_state_allows_pre_run_state_without_an_answer():
    """
    A state that has not run yet legitimately has no answer and nothing to
    review. Constraining that would make the initial state indistinguishable
    from a withheld answer.
    """
    state = _state()
    assert state.answer is None and state.needs_review is False


def test_state_rejects_supported_while_flagged_for_review():
    with pytest.raises(ValueError, match="contradictory"):
        _state(
            answer="Paracetamol 500 mg.",
            supported=True,
            needs_review=True,
            sources=[_source()],
        )


def test_service_asserts_completed_runs_flag_absent_answers():
    """The completed-run fail-closed rule is asserted at the service edge."""
    from app.agent.service import AgentService

    with pytest.raises(AssertionError, match="needs_review=True"):
        AgentService._assert_fail_closed(
            _state(answer=None, supported=False, needs_review=False)
        )


def test_service_accepts_a_correctly_flagged_refusal():
    from app.agent.service import AgentService

    AgentService._assert_fail_closed(
        _state(answer=None, supported=False, needs_review=True)
    )


def test_state_allows_safe_refusal_shape():
    """The honest refusal must remain expressible."""
    state = _state(answer=None, supported=False, needs_review=True)
    assert state.answer is None and state.needs_review is True


def test_state_supports_transient_clearing_then_flagging():
    """
    The safety node's fail-closed update must be accepted by the graph.

    `validate_assignment=True` means a single sequential assignment of
    `answer=None` while `supported` is still True is rejected - correctly, since
    that intermediate state IS contradictory. LangGraph applies a node's whole
    update atomically and validates once, so the real update is fine. This test
    pins that contract: if LangGraph ever changed to per-key validation, the
    safety node would start raising and every unsafe answer would become a 500.
    """
    from langgraph.graph import END, START, StateGraph

    doc = uuid.uuid4()

    def _generate(state: AgentState) -> dict:
        return {
            "grounded_answer": GroundedAnswer(
                answer="Paracetamol 500 mg every six hours.",
                supported=True,
                cited_chunk_ids=["c1"],
            )
        }

    def _clear(state: AgentState) -> dict:
        return {
            "answer": None,
            "supported": False,
            "needs_review": True,
            "grounded_answer": None,
        }

    graph = StateGraph(AgentState)
    graph.add_node("generate", _generate)
    graph.add_node("clear", _clear)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "clear")
    graph.add_edge("clear", END)

    out = graph.compile().invoke(
        _state(discharge_document_id=doc)
    )
    assert out["answer"] is None
    assert out["supported"] is False
    assert out["needs_review"] is True
    assert out["grounded_answer"] is None


def test_state_to_loggable_excludes_clinical_text():
    """
    The log summary must not carry the query, sources, or answer.

    This is the last line of defence for PHI: if a future field were added to
    `to_loggable()` carelessly, this test would show it.
    """
    state = _state(
        answer="Paracetamol 500 mg every six hours.",
        supported=True,
        sources=[_source()],
        grounded_answer=GroundedAnswer(
            answer="Paracetamol 500 mg every six hours.",
            supported=True,
            cited_chunk_ids=["c1"],
        ),
        chunk_text_by_id={"c1": "Paracetamol 500 mg every six hours."},
    )
    blob = repr(state.to_loggable())
    for secret in (
        "Paracetamol",
        "every six hours",
        "What medication",
        "c1",
    ):
        assert secret not in blob


def test_state_repr_hides_retrieved_text():
    """Retrieved chunk text must not leak through repr either."""
    state = _state(retrieved_chunks=[_chunk()], chunk_text_by_id={"c1": "SECRET PHI"})
    assert "SECRET PHI" not in repr(state)


def test_node_trace_deduplicates():
    state = _state()
    state.append_node("a")
    state.append_node("a")
    state.append_node("b")
    assert state.node_trace == ["a", "b"]


# ── Generation error path: fails closed AND logs no clinical text ─────────────


class ExplodingGenerator:
    """
    Generator double raising a non-domain exception whose MESSAGE embeds both
    the user's query and the retrieved document text.

    This stands in for a provider client that echoes a request body in its
    error string - the realistic way clinical text escapes into a log. The
    signature mirrors `GroundedResponseGenerator.generate` exactly.
    """

    provider_name = "fake"
    model = "fake-model"

    @property
    def provider(self):
        return self

    @property
    def name(self):
        return self.provider_name

    def generate(self, *, user_query, chunks, document_id):
        raise RuntimeError(
            f"provider transport failed for query={user_query!r} "
            f"payload={[c.text for c in chunks]!r}"
        )


class StaticRetrieval:
    """Retrieval double returning one real chunk via the real result type."""

    def __init__(self, chunk=None):
        self._chunk = chunk or _chunk(text="SECRETPHI Paracetamol 500 mg.")

    def retrieve(self, *, patient_id, document_id, query, top_k=None):
        from app.rag.retrieval import RetrievalResult

        return RetrievalResult(
            document_id=document_id,
            patient_id=patient_id,
            query_length=len(query),
            indexed_chunks=1,
            min_score=0.0,
            chunks=(self._chunk,),
        )


def test_unexpected_generation_error_fails_closed_without_logging_clinical_text(
    caplog,
):
    """
    The generation node must not hand the query or the passages to the log.

    Generation is the only node that holds both the user's question and the
    verbatim document text, so this is the one place an exception message could
    realistically carry PHI. The fault is contained (no answer, review required)
    and logged by type only.
    """
    query = "What medication was prescribed for the pain relief?"
    graph = build_agent_graph(
        retrieval=StaticRetrieval(),
        generator=ExplodingGenerator(),
        validator=AgentSafetyValidator(),
    )

    with caplog.at_level("DEBUG"):
        out = graph.invoke(
            _state(user_query=query, discharge_document_id=uuid.uuid4())
        )

    state = AgentState.model_validate(out)

    # Contained and fail-closed.
    assert state.answer is None
    assert state.supported is False
    assert state.needs_review is True
    assert state.grounded_answer is None
    assert "generation_failed" in state.errors
    assert state.node_trace[-1] == NODE_SAFE_FALLBACK

    # ...and the fault was still reported, by type, so ops can find it.
    records = [r for r in caplog.records if r.getMessage() == "agent_generation_error"]
    assert len(records) == 1
    assert records[0].error_type == "RuntimeError"

    # No clinical text anywhere in the emitted records, including exception text.
    blob = "\n".join(
        [r.getMessage() for r in caplog.records]
        + [str(r.exc_info) for r in caplog.records if r.exc_info is not None]
    )
    for secret in (query, "SECRETPHI", "Paracetamol", "500 mg", "pain relief"):
        assert secret not in blob
