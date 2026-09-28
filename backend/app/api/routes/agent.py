"""
CareLoop AI - Agent Routes (Phase 4)

Endpoint:
  POST /api/v1/agent/query    grounded, source-traceable answer

SAFETY SHAPE OF THIS ROUTER
  * Requires an explicit `discharge_document_id`, exactly like the Phase 3
    retrieval route.  There is no endpoint that answers across a patient's
    documents or across patients, so a missing authorisation check can never
    widen the blast radius.
  * `answer=null` is a SUCCESS (HTTP 200), not an error.  "This document does
    not cover your question" is a truthful answer and returning 404 or 500 for
    it would pressure a caller into retrying or inventing something.  Only
    genuinely broken requests get a non-200.
  * The route adds no authorisation logic of its own. Ownership is enforced in
    `RagRetrievalService` against PostgreSQL, so the guarantee holds even if
    this route were bypassed and the service called directly.
  * Nothing here logs the query, the sources, or the answer.  The service logs
    a PHI-free summary and the route logs nothing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth_deps import require_agent_query_patient_access
from app.api.deps import AgentServiceDep
from app.core.logging import get_logger
from app.schemas.agent import (
    GroundedAnswerRequest,
    GroundedAnswerResponse,
    GroundedSourceResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["Agent"])


def _to_response(result) -> GroundedAnswerResponse:
    """
    Project an `AgentResult` onto the strict response schema.

    The projection is explicit rather than a `from_result` classmethod so the
    field-by-field mapping is visible at the point where the internal model
    becomes an external contract.  Only `chunk_id`, `source_page`, and `score`
    cross the boundary; retrieved text never does.
    """
    return GroundedAnswerResponse(
        answer=result.answer,
        supported=result.supported,
        needs_review=result.needs_review,
        safety_flags=result.safety_flags,
        sources=[
            GroundedSourceResponse(
                chunk_id=source.chunk_id,
                source_page=source.source_page,
                score=source.score,
            )
            for source in result.sources
        ],
        trace_id=result.trace_id,
        llm_provider=result.llm_provider,
    )


@router.post(
    "/agent/query",
    response_model=GroundedAnswerResponse,
    summary="Answer a question grounded in one discharge document",
    responses={
        404: {"description": "Document not found for this patient"},
        422: {"description": "Request is empty or invalid"},
        502: {"description": "LLM provider returned an unusable response"},
        503: {"description": "Provider not configured, or vector store unavailable"},
    },
    dependencies=[Depends(require_agent_query_patient_access)],
)
def query_document(
    payload: GroundedAnswerRequest, service: AgentServiceDep
) -> GroundedAnswerResponse:
    """
    Answer a question using only one patient's discharge document.

    The answer is a report of what the document says. It is not a diagnosis,
    not a treatment recommendation, and not a substitute for the care team.

    Read the response as a unit:
      * `supported=true`  -> `answer` is set and `sources` is non-empty. The
        answer's vocabulary is drawn from the cited passages and passed
        independent safety validation.
      * `supported=false` -> `answer` is null, `needs_review` is true, and
        `safety_flags` explains why. Check `safety_flags` to distinguish
        "the document does not cover this" from "the model's answer was
        rejected".

    A null answer is a designed, safe outcome - not a server error. Lexical
    grounding reduces unsupported content; it does not certify medical
    correctness, so any output here should be read by a clinician before it
    reaches a patient.
    """
    result = service.query(
        patient_id=payload.patient_id,
        discharge_document_id=payload.discharge_document_id,
        user_query=payload.query,
        top_k=payload.top_k,
    )
    return _to_response(result)
