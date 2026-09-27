"""
CareLoop AI - Agent Service (Phase 4)

The public entry point to the agent.  Everything above this layer (routes,
tests) sees one object with one method and never touches the graph.

WHY THIS EXISTS
`graph.invoke()` returns a plain `dict`, not the `AgentState` model it was
given.  That is a LangGraph behaviour, not a bug, and it is the seam where the
typed-state guarantee would quietly end.  `query()` re-validates that dict back
into `AgentState` and then projects it onto the strict response schema, so
callers cannot receive a shape the state model would have rejected.

It also keeps the graph's collaborators out of the route layer: the route
receives a result object, not a graph.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, List, Optional

from app.agent.generation import GroundedResponseGenerator
from app.agent.graph import (
    ALL_NODES,
    build_agent_graph,
    new_trace_id,
)
from app.agent.safety import AgentSafetyValidator
from app.agent.state import AgentState, GroundedSource
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.rag.retrieval import RagRetrievalService

logger = get_logger(__name__)


class AgentResult:
    """
    A completed agent run, ready to be projected onto the API schema.

    Holds no clinical text beyond the answer and the source metadata, so it is
    safe to hand to a route.  `to_loggable()` is PHI-free by construction.
    """

    __slots__ = (
        "answer",
        "supported",
        "needs_review",
        "safety_flags",
        "sources",
        "trace_id",
        "llm_provider",
        "llm_model",
        "route",
        "errors",
        "node_trace",
        "latency_ms",
    )

    def __init__(self, *, state: AgentState) -> None:
        self.answer = state.answer
        self.supported = state.supported
        self.needs_review = state.needs_review
        self.safety_flags: List[str] = [flag.value for flag in state.safety_flags]
        self.sources: List[GroundedSource] = list(state.sources)
        self.trace_id = state.trace_id
        self.llm_provider = state.llm_provider
        self.llm_model = state.llm_model
        self.route = state.route.value
        self.errors = list(state.errors)
        self.node_trace = list(state.node_trace)
        self.latency_ms = state.latency_ms

    def to_loggable(self) -> dict[str, Any]:
        """PHI-free run summary. No query, no source text, no answer."""
        return {
            "trace_id": self.trace_id,
            "route": self.route,
            "nodes": self.node_trace,
            "source_count": len(self.sources),
            "supported": self.supported,
            "needs_review": self.needs_review,
            "safety_flags": self.safety_flags,
            "error_count": len(self.errors),
            "latency_ms": self.latency_ms,
            "llm_provider": self.llm_provider,
        }


class AgentService:
    """
    Orchestrates one grounded answer per request.

    Stateless between calls: the compiled graph holds no per-patient state, so
    concurrent requests cannot observe each other.
    """

    def __init__(
        self,
        *,
        retrieval: RagRetrievalService,
        generator: GroundedResponseGenerator,
        validator: Optional[AgentSafetyValidator] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._retrieval = retrieval
        self._generator = generator
        self._validator = validator or AgentSafetyValidator()
        self._graph = build_agent_graph(
            retrieval=retrieval,
            generator=generator,
            validator=self._validator,
            settings=self._settings,
        )

    @property
    def graph(self) -> Any:
        """The compiled graph. Exposed for topology assertions in tests."""
        return self._graph

    @property
    def nodes(self) -> tuple[str, ...]:
        return ALL_NODES

    # ── Main entry point ────────────────────────────────────────────────────

    def query(
        self,
        *,
        patient_id: uuid.UUID,
        discharge_document_id: uuid.UUID,
        user_query: str,
        top_k: Optional[int] = None,
    ) -> AgentResult:
        """
        Run the graph once and return a validated result.

        This method does not raise for ordinary failures.  An invalid request,
        a retrieval fault, a provider outage, or a rejected answer all come back
        as a result with `answer=None`, `supported=False`, and
        `needs_review=True`.  The caller decides how to present that; hiding it
        behind a 500 would lose the distinction between "the document does not
        cover this" and "the system is broken", which is the information a
        user most needs.
        """
        trace_id = new_trace_id()
        started = time.perf_counter()

        initial = AgentState(
            patient_id=patient_id,
            discharge_document_id=discharge_document_id,
            user_query=user_query,
            top_k=top_k,
            trace_id=trace_id,
        )

        raw = self._graph.invoke(initial)
        # Re-establish the typed guarantee that invoke() gives back.
        final = AgentState.model_validate(raw)
        # `node_trace` is recorded by the nodes themselves, so it reflects the
        # path actually taken.  It is deliberately NOT backfilled with
        # ALL_NODES when empty: a fabricated trace would report nodes that never
        # ran, which is worse than an empty one for diagnosing a run.
        final.latency_ms = final.latency_ms or int(
            (time.perf_counter() - started) * 1000
        )

        self._assert_fail_closed(final)

        result = AgentResult(state=final)
        logger.info(
            "agent_query_completed",
            extra=result.to_loggable(),
        )
        return result

    @staticmethod
    def _assert_fail_closed(state: AgentState) -> None:
        """
        Assert the completed-run invariant.

        A finished run with no answer must say so via `needs_review`.  This is
        the one place that knows the run is complete, so it is the one place
        the rule can be enforced without also constraining the pre-run state.

        Raises rather than repairs: reaching this with `needs_review=False` would
        mean a node set a contradictory combination, and silently correcting it
        would hide a real defect in the graph.
        """
        if state.answer is None and not state.needs_review:
            raise AssertionError(
                "completed agent run returned answer=None without "
                "needs_review=True"
            )


__all__ = ["AgentResult", "AgentService"]
