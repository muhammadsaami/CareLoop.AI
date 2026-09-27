"""
CareLoop AI - Agent Graph (Phase 4)

The LangGraph `StateGraph` that defines the whole agent.  Read this module to
know exactly what the system does.

    ┌──────────────────┐
    │  validate_request│  reject empty / malformed input before any spend
    └────────┬─────────┘
             │ always
    ┌────────▼──────────────────┐
    │ retrieve_grounded_context │  Phase 3 RagRetrievalService, unchanged
    └────────┬──────────────────┘
             │ always
       ┌─────┴──────┐
  no chunks      chunks
       │             │
  ┌────▼─────┐  ┌────▼────────────────────┐
  │  (end)   │  │ generate_grounded_response│
  └──────────┘  └────┬────────────────────┘
                    │ always
              ┌─────▼────────┐
              │validate_safety│  independent re-check, no LLM
              └─────┬────────┘
                    │
              ┌─────┴──────┐
            safe        unsafe
              │             │
       ┌──────▼──────┐  ┌───▼──────────┐
       │  (end)      │  │ safe_fallback│
       └─────────────┘  └──────┬───────┘
                                │ always
                          ┌─────▼─────┐
                          │   (end)   │
                          └───────────┘

DETERMINISTISM IS THE POINT
Every edge is a plain function of state.  No node chooses its own successor, no
model output influences control flow, and there are no cycles.  The same
request against the same index always traverses the same path, which is what
makes the behaviour testable and auditable.

WHY THERE IS NO AGENT LOOP
A loop would let the model request more retrieval, re-rank, or retry until it
felt confident - converting a bounded, inspectable pipeline into an unbounded
one whose cost and behaviour depend on model output.  Phase 4 deliberately
has no `END`-to-`START` edge.  A multi-step or self-correcting agent is a
future phase with a different risk profile, not a feature to add here.

Every node is total: it returns a state update, never raises for ordinary
failures.  Retrieval errors become flags; provider errors become the fallback
response.  An exception here would surface as a 500 with no explanation of
what the system chose not to say, which is the wrong behaviour for a clinical
adjacent tool.
"""
from __future__ import annotations

import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from langgraph.graph import END, START, StateGraph

from app.agent.generation import GroundedResponseGenerator
from app.agent.safety import AgentSafetyValidator
from app.agent.state import (
    AgentState,
    GroundedSource,
    Route,
    SafetyFlagKind,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import CareLoopError
from app.core.logging import get_logger
from app.rag.retrieval import RagRetrievalService

logger = get_logger(__name__)

#: Node names, exported so tests and docs cannot drift from the graph.
NODE_VALIDATE_REQUEST = "validate_request"
NODE_RETRIEVE = "retrieve_grounded_context"
NODE_GENERATE = "generate_grounded_response"
NODE_VALIDATE_SAFETY = "validate_safety"
NODE_SAFE_FALLBACK = "safe_fallback"

ALL_NODES: Tuple[str, ...] = (
    NODE_VALIDATE_REQUEST,
    NODE_RETRIEVE,
    NODE_GENERATE,
    NODE_VALIDATE_SAFETY,
    NODE_SAFE_FALLBACK,
)


# ── Routing (pure functions of state; no model input) ────────────────────────


def route_after_validation(state: AgentState) -> Route:
    """
    Reject invalid requests before spending a retrieval or a provider call.

    An empty or whitespace-only query can never be answered from a document,
    so it terminates immediately rather than becoming a model request.
    """
    if not state.user_query or not state.user_query.strip():
        return Route.NO_CONTEXT
    return Route.VALIDATED


def route_after_retrieval(state: AgentState) -> Route:
    """Proceed only when retrieval returned at least one usable chunk."""
    if state.sources:
        return Route.VALIDATED
    return Route.NO_CONTEXT


def route_after_generation(state: AgentState) -> Route:
    """
    Proceed to safety validation only when a draft actually exists.

    A provider outage or malformed payload leaves no draft to check, and
    running `validate_safety` over nothing would overwrite the real diagnosis
    (`provider_unavailable`) with a misleading one (`invalid_model_output`).
    Those failures are already terminal, so they go straight to the fallback.
    """
    if state.grounded_answer is None:
        return Route.UNSAFE
    return Route.VALIDATED


def route_after_safety(state: AgentState) -> Route:
    """
    Emit the answer only when safety cleared it.

    The draft's own `supported` claim is deliberately NOT consulted here.  The
    model's belief about itself has no bearing on whether the answer is shown.
    """
    if state.needs_review or state.answer is None:
        return Route.UNSAFE
    return Route.SAFE


# ── Node implementations ─────────────────────────────────────────────────────


def _validate_request(state: AgentState) -> Dict[str, Any]:
    """Normalise and sanity-check the request. Never fails closed itself."""
    query = (state.user_query or "").strip()

    if not query:
        return {
            "node_trace": [NODE_VALIDATE_REQUEST],
            "safety_flags": [SafetyFlagKind.INSUFFICIENT_EVIDENCE],
            "needs_review": True,
            "supported": False,
            "answer": None,
            "route": Route.NO_CONTEXT,
            "errors": ["empty_query"],
        }

    # `user_query` is min_length=1, so the stripped query is always valid.
    return {
        "user_query": query,
        "route": Route.VALIDATED,
        "node_trace": [NODE_VALIDATE_REQUEST],
    }


def _retrieve_grounded_context(
    state: AgentState,
    *,
    retrieval: RagRetrievalService,
    max_top_k: int = 8,
) -> Dict[str, Any]:
    """
    Retrieve evidence through the EXISTING Phase 3 service.

    There is no Chroma access in this module and no second filtering path.
    Patient scoping, document scoping, and embedding-fingerprint validation
    are enforced by `RagRetrievalService` / `vector_store.py` exactly as in
    Phase 3, and are not re-implemented here.

    `sources` is populated from real chunk metadata.  `retrieved_chunk_ids` is
    carried alongside so the safety node can detect a citation the model made
    up, which is impossible if the set is built from retrieval alone.
    """
    if route_after_validation(state) is Route.NO_CONTEXT:
        return {
            "node_trace": [NODE_RETRIEVE],
            "sources": [],
            "retrieved_chunk_ids": [],
            "retrieved_chunks": None,
            "chunk_text_by_id": None,
            "route": Route.NO_CONTEXT,
            "answer": None,
            "supported": False,
            "needs_review": True,
        }

    # A client-supplied top_k is bounded by configuration, so one request
    # cannot inflate its own prompt or cost. `None` means "use the Phase 3
    # default", which is left to the retrieval service.
    effective_top_k = state.top_k
    if effective_top_k is not None:
        effective_top_k = min(effective_top_k, max(1, max_top_k))

    started = time.perf_counter()
    try:
        result = retrieval.retrieve(
            patient_id=state.patient_id,
            document_id=state.discharge_document_id,
            query=state.user_query,
            top_k=effective_top_k,
        )
        # Built INSIDE the try so that a fault while interpreting the result -
        # a malformed chunk, a source that fails validation - is contained and
        # fails closed, exactly like a fault in the call itself.
        sources: List[GroundedSource] = [
            GroundedSource(
                discharge_document_id=state.discharge_document_id,
                chunk_id=chunk.chunk_id,
                source_page=chunk.source_page,
                score=chunk.score,
            )
            for chunk in result.chunks
        ]
    except CareLoopError:
        # Domain errors PROPAGATE, exactly as they do through the Phase 3
        # retrieval route.  Each one already carries the right status code:
        #   RetrievalForbiddenError / DocumentNotFoundError -> 404
        #   VectorStoreError / EmbeddingError                -> 503
        #   EmbeddingFingerprintMismatchError                -> 409
        #
        # Swallowing them into a 200 with `answer=null` would be actively
        # misleading: a client would read "this document does not cover your
        # question" when the truth is "you may not access this document" or
        # "the vector store is down".  An authorization failure must not be
        # dressed up as a refusal, and an outage must be retryable rather than
        # reported as a settled answer.
        #
        # Only *unanticipated* exceptions are contained below.
        logger.warning(
            "agent_retrieval_rejected",
            extra={
                "trace_id": state.trace_id,
                "error_type": type(sys.exc_info()[1]).__name__,
            },
        )
        raise
    except Exception:
        # Unexpected: an unforeseen fault such as a corrupt Chroma segment.
        # Contained so a client never receives a stack trace, and still fails
        # closed with nothing asserted.
        #
        # `logger.exception` is the codebase-wide convention for unforeseen
        # faults and is kept for consistency. It is safe here because provider
        # and vector-store failures are `CareLoopError`s, which are re-raised
        # above with only their TYPE logged and no exception text. What reaches
        # this branch is a code-level fault (TypeError, KeyError) whose message
        # describes the bug, not the data: the query, the retrieved passages,
        # and the model output are never passed to the log.
        logger.exception(
            "agent_retrieval_error",
            extra={"trace_id": state.trace_id},
        )
        return {
            "node_trace": [NODE_RETRIEVE],
            "sources": [],
            "route": Route.NO_CONTEXT,
            "answer": None,
            "supported": False,
            "needs_review": True,
            "safety_flags": [SafetyFlagKind.PROVIDER_UNAVAILABLE],
            "errors": ["retrieval_unavailable"],
            "retrieved_chunks": None,
            "chunk_text_by_id": None,
        }

    updates: Dict[str, Any] = {
        "node_trace": [NODE_RETRIEVE],
        "sources": sources,
        "retrieved_chunk_ids": [chunk.chunk_id for chunk in result.chunks],
        "retrieved_chunks": list(result.chunks),
        "chunk_text_by_id": {
            chunk.chunk_id: chunk.text for chunk in result.chunks
        },
        "min_score": result.min_score,
        "indexed_chunks": result.indexed_chunks,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "route": route_after_retrieval(_with_sources(state, sources)),
    }

    # Zero matches terminates at END without visiting generate or validate_safety,
    # so the withheld-answer shape has to be set HERE.  Missing this is a real
    # bug, not a cosmetic one: the run would finish with answer=None and
    # needs_review=False, presenting "nothing found" as a settled result and
    # tripping the service's completed-run assertion.
    if not sources:
        updates.update(
            answer=None,
            supported=False,
            needs_review=True,
            safety_flags=[SafetyFlagKind.NO_RETRIEVAL_MATCH],
            retrieved_chunks=None,
            chunk_text_by_id=None,
        )
    return updates


def _generate_grounded_response(
    state: AgentState,
    *,
    generator: GroundedResponseGenerator,
) -> Dict[str, Any]:
    """
    Produce a schema-valid draft through the Phase 2 provider abstraction.

    The LLM contributes answer text and chunk ids only.  It never contributes a
    page number, a document id, or a patient id: `generation.py` resolves
    citations against real retrieved chunks and copies provenance from them.
    """
    started = time.perf_counter()
    provider_name = model = None
    try:
        provider = generator.provider
        provider_name, model = provider.name, provider.model
    except CareLoopError:
        pass

    try:
        draft, sources, provider_name, model = generator.generate(
            user_query=state.user_query,
            chunks=state.retrieved_chunks or [],
            document_id=state.discharge_document_id,
        )
    except CareLoopError:
        # Provider errors PROPAGATE, exactly as they do through the Phase 2
        # extraction path, so the client sees the same status codes:
        #   ProviderNotConfiguredError -> 503  (actionable: set the API key)
        #   ProviderTimeoutError       -> 504  (retryable)
        #   ProviderRateLimitError     -> 429  (retryable, back off)
        #   ProviderAuthenticationError -> 502
        #
        # Reporting these as `answer=null` with HTTP 200 would tell the user
        # "your document does not cover this" when the truth is that the
        # provider is unreachable.  That misreading is worse than an error,
        # because it looks like a settled answer.
        logger.warning(
            "agent_generation_rejected",
            extra={
                "trace_id": state.trace_id,
                "error_type": type(sys.exc_info()[1]).__name__,
            },
        )
        raise
    except Exception:
        # Unforeseen fault inside generation (including a provider returning a
        # payload so broken that coercion itself fails).  Contained so no stack
        # trace reaches the client, and still fails closed.
        #
        # No `exc_info` is attached on purpose. This is the one node holding the
        # user's query and the retrieved document text, and a provider client
        # can embed a request body in its error message. The query, the
        # passages, and the generated answer are therefore never handed to the
        # log here; `error_type` below is enough to identify the fault.
        logger.error(
            "agent_generation_error",
            extra={
                "trace_id": state.trace_id,
                "llm_provider": provider_name,
                "error_type": type(sys.exc_info()[1]).__name__,
            },
        )
        return {
            "node_trace": [NODE_GENERATE],
            "grounded_answer": None,
            "llm_provider": provider_name,
            "llm_model": model,
            "route": Route.UNSAFE,
            "answer": None,
            "supported": False,
            "needs_review": True,
            "safety_flags": [SafetyFlagKind.INVALID_MODEL_OUTPUT],
            "errors": ["generation_failed"],
            "latency_ms": _elapsed(state, started),
        }

    return {
        "node_trace": [NODE_GENERATE],
        "grounded_answer": draft,
        "sources": sources,
        "llm_provider": provider_name,
        "llm_model": model,
        "retrieved_chunks": None,
        "latency_ms": _elapsed(state, started),
        # Recorded for observability; control flow comes from the conditional
        # edge, which calls route_after_generation() with the real merged state.
        "route": Route.VALIDATED,
    }


def _validate_safety(
    state: AgentState,
    *,
    validator: AgentSafetyValidator,
) -> Dict[str, Any]:
    """
    Independently re-check the draft before anything is shown to a user.

    Assumes the model did not comply with its prompt.  A failure here is
    withheld, never repaired: editing clinical wording is a medical judgement
    this system does not make.
    """
    assessment = validator.validate(
        draft=state.grounded_answer,
        sources=state.sources,
        retrieved_chunk_ids=set(state.retrieved_chunk_ids or []),
        chunk_text_by_id=state.chunk_text_by_id or None,
    )

    # Strip chunk text from state as soon as safety is done with it: it must
    # not be able to reach the response or a log line.
    updates: Dict[str, Any] = {
        "node_trace": [NODE_VALIDATE_SAFETY],
        "retrieved_chunks": None,
        "chunk_text_by_id": None,
        "safety_flags": assessment.flags,
        # Recorded for observability. Control flow actually comes from the
        # conditional edge on this node, which calls route_after_safety() with
        # the real merged state.
        "route": Route.UNSAFE if assessment.flags else Route.SAFE,
    }

    if not assessment.safe:
        updates["answer"] = None
        updates["supported"] = False
        updates["needs_review"] = True
        # Overwriting the draft guarantees an unvalidated string cannot be
        # read by a later node even by accident.
        updates["grounded_answer"] = None
        return updates

    draft = state.grounded_answer
    if draft is not None and draft.answer:
        # Promotion: a validated draft becomes the final answer.  This must be
        # explicit - the validated draft lives in `grounded_answer`, and
        # `answer` is a separate channel that only this node may fill.  Relying
        # on a default would report a passing run as a refusal.
        updates["answer"] = draft.answer
        updates["supported"] = True
        updates["needs_review"] = False
        return updates

    # A safe assessment with no answer is a genuine refusal: the validator
    # already rejects the contradictory "supported but empty" case, so reaching
    # here means the model declined.  That is a correct outcome and keeps its
    # (empty) flag list; only `needs_review` is added so the caller can see
    # nothing was established.
    updates["answer"] = None
    updates["supported"] = False
    updates["needs_review"] = True
    return updates


def _safe_fallback(state: AgentState) -> Dict[str, Any]:
    """
    The single terminal path for every withheld answer.

    Returns `answer=None`, `supported=False`, `needs_review=True` and the
    flag explaining why.  No clinical text is produced, so there is nothing
    here that could be misread as advice.
    """
    return {
        "node_trace": [NODE_SAFE_FALLBACK],
        "answer": None,
        "supported": False,
        "needs_review": True,
        "route": Route.UNSAFE,
    }


# ── Graph assembly ───────────────────────────────────────────────────────────


def build_agent_graph(
    *,
    retrieval: RagRetrievalService,
    generator: GroundedResponseGenerator,
    validator: Optional[AgentSafetyValidator] = None,
    settings: Optional[Settings] = None,
):
    """
    Build and compile the agent graph.

    Collaborators are injected rather than constructed here, which is what lets
    tests exercise every failure path with a fake provider and no network.
    """
    validator = validator or AgentSafetyValidator()
    settings = settings or get_settings()

    def _validate_request_node(state: AgentState) -> Dict[str, Any]:
        return _validate_request(state)

    def _retrieve_node(state: AgentState) -> Dict[str, Any]:
        return _retrieve_grounded_context(
            state,
            retrieval=retrieval,
            max_top_k=int(getattr(settings, "agent_max_top_k", 8)),
        )

    def _generate_node(state: AgentState) -> Dict[str, Any]:
        return _generate_grounded_response(state, generator=generator)

    def _validate_safety_node(state: AgentState) -> Dict[str, Any]:
        return _validate_safety(state, validator=validator)

    graph = StateGraph(AgentState)

    graph.add_node(NODE_VALIDATE_REQUEST, _validate_request_node)
    graph.add_node(NODE_RETRIEVE, _retrieve_node)
    graph.add_node(NODE_GENERATE, _generate_node)
    graph.add_node(NODE_VALIDATE_SAFETY, _validate_safety_node)
    graph.add_node(NODE_SAFE_FALLBACK, _safe_fallback)

    graph.add_edge(START, NODE_VALIDATE_REQUEST)

    # Conditional edges. The router is a pure function of state; see
    # route_after_* above for the full justification of each branch.
    #
    # NOTE: `validate_request` and `retrieve_grounded_context` are reachable
    # only through these conditional edges. Adding an unconditional
    # `add_edge` alongside them would run retrieval for an empty query and
    # quietly undo the cost guard, so do not add one.
    graph.add_conditional_edges(
        NODE_VALIDATE_REQUEST,
        route_after_validation,
        {Route.VALIDATED: NODE_RETRIEVE, Route.NO_CONTEXT: END},
    )
    graph.add_conditional_edges(
        NODE_RETRIEVE,
        route_after_retrieval,
        {Route.VALIDATED: NODE_GENERATE, Route.NO_CONTEXT: END},
    )
    graph.add_conditional_edges(
        NODE_GENERATE,
        route_after_generation,
        {Route.VALIDATED: NODE_VALIDATE_SAFETY, Route.UNSAFE: NODE_SAFE_FALLBACK},
    )
    graph.add_conditional_edges(
        NODE_VALIDATE_SAFETY,
        route_after_safety,
        {Route.SAFE: END, Route.UNSAFE: NODE_SAFE_FALLBACK},
    )
    graph.add_edge(NODE_SAFE_FALLBACK, END)

    compiled = graph.compile()

    if settings.agent_log_graph_topology:
        # Node and edge names only - no state, no query, no document text.
        logger.info(
            "agent_graph_compiled",
            extra={"nodes": list(ALL_NODES), "cycles": False},
        )

    return compiled


def new_trace_id() -> str:
    """Correlation id for one agent run. Carries no request content."""
    return uuid.uuid4().hex


def _elapsed(state: AgentState, started: float) -> int:
    """Latest node latency, replacing rather than summing the retrieval time."""
    return int((time.perf_counter() - started) * 1000)


def _with_sources(state: AgentState, sources: List[GroundedSource]) -> AgentState:
    """Minimal state view for a pure router call; avoids mutating the real one."""
    return AgentState(
        patient_id=state.patient_id,
        discharge_document_id=state.discharge_document_id,
        user_query=state.user_query,
        top_k=state.top_k,
        sources=sources,
    )


__all__ = [
    "ALL_NODES",
    "NODE_GENERATE",
    "NODE_RETRIEVE",
    "NODE_SAFE_FALLBACK",
    "NODE_VALIDATE_REQUEST",
    "NODE_VALIDATE_SAFETY",
    "build_agent_graph",
    "new_trace_id",
    "route_after_generation",
    "route_after_retrieval",
    "route_after_safety",
    "route_after_validation",
]
