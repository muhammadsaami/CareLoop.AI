"""
CareLoop AI - LangGraph Agent State (Phase 4)

TYPED GRAPH STATE
`AgentState` is a Pydantic model, and it is the LangGraph `StateGraph` schema.
LangGraph uses the model to derive its channel layout, and a node receives a
real `AgentState` instance - so every node reads validated attributes rather
than reaching into an untyped dict.

The one sharp edge: `graph.invoke()` returns a plain `dict`, not the model.
`AgentService` therefore re-validates that dict back into `AgentState` at the
boundary (see `service.py`), which keeps the type guarantee intact from the
API response inward.

WHAT IS DELIBERATELY NOT IN THE STATE
  * No free-form `context: dict[str, Any]`.  Retrieved evidence is a list of
    `GroundedSource`, and a safety concern is a `SafetyFlag` with a fixed
    `SafetyFlagKind`.  An untyped bag is where "the model said so" quietly
    becomes "the system knows so", and that is the failure this whole phase
    exists to prevent.
  * No raw LLM text.  The model's free-text answer is validated into
    `GroundedAnswer` before it is stored, so a node downstream can never
    read an unvalidated string as if it were a result.
  * No conversation history.  Phase 4 is stateless per request.

FAIL-CLOSED INVARIANT
`model_validator` enforces the shape that makes an unsafe response
unrepresentable rather than merely discouraged:
  * `supported=True` requires a non-empty answer AND at least one source.
  * `answer=None` forces `supported=False` AND `needs_review=True`.
  * every source must belong to the requested document.
"""
from __future__ import annotations

import operator
import uuid
from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Enumerations (closed sets, not free strings) ──────────────────────────────


class SafetyFlagKind(str, Enum):
    """
    Why an answer was held back.

    A closed enum rather than free text: the API contract stays stable, and a
    caller can branch on it without parsing prose.  Each value names a
    LIMITATION of this system, never a clinical judgement.
    """

    NO_RETRIEVAL_MATCH = "no_retrieval_match"
    UNSUPPORTED_BY_SOURCES = "unsupported_by_sources"
    FABRICATED_SOURCE = "fabricated_source"
    MEDICAL_OVERREACH = "medical_overreach"
    GROUNDING_NOT_ESTABLISHED = "grounding_not_established"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Route(str, Enum):
    """Deterministic routing outcomes. No LLM ever selects one of these."""

    VALIDATED = "validated"
    NO_CONTEXT = "no_context"
    SAFE = "safe"
    UNSAFE = "unsafe"


# ── Retrieved evidence ───────────────────────────────────────────────────────


class GroundedSource(BaseModel):
    """
    One piece of supporting evidence, built from REAL retrieved chunk data.

    `chunk_id` and `source_page` are copied from the Phase 3 retrieval result
    by `generation.py`.  They are never produced by the language model: the
    model may only *name* a chunk id it used, and the code resolves that name
    against the chunks actually retrieved.  A fabricated page number is
    therefore not expressible through this type.
    """

    model_config = ConfigDict(extra="forbid")

    discharge_document_id: uuid.UUID
    chunk_id: str
    source_page: Optional[int] = Field(
        default=None,
        description=(
            "Page the text came from. Null when the source document carried no "
            "page marker. Never estimated and never defaulted to 1."
        ),
    )
    score: float = Field(
        ..., ge=0.0, le=1.0, description="Retrieval similarity for this chunk"
    )


class GroundedAnswer(BaseModel):
    """
    The model's answer after schema validation - NOT after safety validation.

    Existence in the state does not mean the answer is safe to return; that is
    decided by `validate_safety`.  `cited_chunk_ids` are the only thing the
    model controls, and each must resolve to a real retrieved chunk.
    """

    model_config = ConfigDict(extra="forbid")

    answer: Optional[str] = Field(
        default=None,
        description="Formulated answer, or null when the model declined",
    )
    supported: bool = Field(
        ..., description="Model's own claim that the sources support an answer"
    )
    cited_chunk_ids: List[str] = Field(
        default_factory=list,
        description=(
            "Chunk ids the model says it used. Validated against the retrieved "
            "chunks; anything unrecognised is treated as fabricated."
        ),
    )
    model_declined_reason: Optional[str] = Field(
        default=None,
        description="Why the model returned no answer; audit metadata only"
    )

    @field_validator("answer", "model_declined_reason")
    @classmethod
    def _strip_or_null(cls, value: Optional[str]) -> Optional[str]:
        """Treat an empty or whitespace-only string as genuinely absent."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


# ── Graph state ───────────────────────────────────────────────────────────────


class AgentState(BaseModel):
    """
    The state threaded through the agent graph.

    Mutable fields carry the channel updates LangGraph merges.  The immutable
    request fields are included so a node can always see what it was asked
    without reaching back into the caller.
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── Request (immutable for the life of the graph run) ───────────────────
    patient_id: uuid.UUID
    discharge_document_id: uuid.UUID
    user_query: str = Field(..., min_length=1, max_length=2000)
    top_k: Optional[int] = None

    # ── Retrieved evidence ──────────────────────────────────────────────────
    sources: List[GroundedSource] = Field(default_factory=list)

    #: Chunk ids that actually came back from Phase 3 retrieval.  Kept
    #: separately from `sources` so the safety node can tell the difference
    #: between "retrieved and cited" and "cited by the model only" - which is
    #: precisely what makes a fabricated citation detectable.
    retrieved_chunk_ids: List[str] = Field(default_factory=list)

    #: Retrieved chunk text, held only between the retrieve and safety nodes
    #: and cleared by `validate_safety`.  Deliberately not part of `sources`:
    #: keeping clinical text out of the response model and out of
    #: `to_loggable()` makes an accidental PHI log leak a type error rather
    #: than a code-review question.
    retrieved_chunks: Optional[List[Any]] = Field(default=None, repr=False)
    chunk_text_by_id: Optional[Dict[str, str]] = Field(
        default=None, repr=False
    )

    min_score: Optional[float] = None
    indexed_chunks: Optional[int] = None

    # ── Model output (schema-validated, not yet safety-checked) ─────────────
    grounded_answer: Optional[GroundedAnswer] = None

    # ── Safety outcome ──────────────────────────────────────────────────────
    safety_flags: List[SafetyFlagKind] = Field(default_factory=list)
    needs_review: bool = False

    # ── Final result ────────────────────────────────────────────────────────
    answer: Optional[str] = None
    supported: bool = False
    route: Route = Route.VALIDATED

    # ── Execution metadata (safe to log; never contains clinical text) ──────
    trace_id: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None

    #: Path actually taken, in order.  `Annotated` with `add` is REQUIRED: a
    #: bare list field is a last-value-wins channel in LangGraph, so each node
    #: would overwrite the previous node's entry and the trace would only ever
    #: show one node. With the reducer, each node contributes just its own name
    #: and the channel accumulates.
    node_trace: Annotated[List[str], operator.add] = Field(default_factory=list)

    latency_ms: Optional[int] = None

    # ── Errors ──────────────────────────────────────────────────────────────
    # A short, client-safe code.  Never an exception string, a provider
    # payload, or any document content.
    errors: List[str] = Field(default_factory=list)

    # ── Invariants ──────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _enforce_fail_closed_shape(self) -> "AgentState":
        """
        Invariants that hold at every point in a run.

        `answer=None requires needs_review=True` is deliberately NOT here: a
        state that has not run yet has no answer and has nothing to review, and
        forcing the flag on at construction time would make the initial state
        indistinguishable from a withheld answer. That rule is about a
        COMPLETED run, so it is asserted in `AgentService.query` (which is the
        only place that knows the run is finished) and again in the API
        response model (which is the external contract).
        """
        if self.supported:
            if not self.answer or not self.answer.strip():
                raise ValueError(
                    "supported=True requires a non-empty answer"
                )
            if not self.sources:
                raise ValueError(
                    "supported=True requires at least one source"
                )
            if self.needs_review:
                raise ValueError(
                    "supported=True and needs_review=True are contradictory"
                )
        if self.answer is None and self.supported:
            raise ValueError("answer=None cannot be supported=True")
        return self

    # ── Convenience ─────────────────────────────────────────────────────────

    @property
    def has_context(self) -> bool:
        return bool(self.sources)

    def append_node(self, name: str) -> None:
        """Record node execution for the run trace (no payloads)."""
        if name not in self.node_trace:
            self.node_trace.append(name)

    def to_loggable(self) -> dict[str, Any]:
        """
        Safe, PHI-free summary for observability.

        Counts, flags, and identifiers only.  Deliberately excludes the query,
        the source text, and the answer.
        """
        return {
            "trace_id": self.trace_id,
            "route": self.route.value,
            "nodes": list(self.node_trace),
            "source_count": len(self.sources),
            "min_score": self.min_score,
            "supported": self.supported,
            "needs_review": self.needs_review,
            "safety_flags": [flag.value for flag in self.safety_flags],
            "error_count": len(self.errors),
            "latency_ms": self.latency_ms,
        }


__all__ = [
    "AgentState",
    "GroundedAnswer",
    "GroundedSource",
    "Route",
    "SafetyFlagKind",
]
