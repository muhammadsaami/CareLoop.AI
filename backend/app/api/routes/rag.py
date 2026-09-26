"""
CareLoop AI - RAG Routes (Phase 3)

Endpoints:
  POST /api/v1/rag/retrieve                          grounded chunk search
  POST /api/v1/rag/documents/{document_id}/index     explicit indexing

SAFETY SHAPE OF THIS ROUTER
  * Retrieval requires an explicit `document_id` in the body.  There is no
    endpoint that searches across a patient's documents or across patients,
    so a missing authorisation check can never widen the blast radius.
  * No endpoint returns a generated answer.  Both routes return verbatim
    source text (or counts), never advice.
  * Ownership is enforced in the service layer against PostgreSQL; the route
    adds nothing that could be bypassed by calling the service directly.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import RagIndexingServiceDep, RagRetrievalServiceDep
from app.core.logging import get_logger
from app.schemas.rag import (
    RagIndexRequest,
    RagIndexResponse,
    RagRetrieveRequest,
    RagRetrieveResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["RAG Retrieval"])


@router.post(
    "/rag/retrieve",
    response_model=RagRetrieveResponse,
    summary="Search a discharge document for grounded source excerpts",
    responses={
        404: {"description": "Document not found for this patient"},
        422: {"description": "Query is empty or invalid"},
        502: {"description": "Embedding provider failed"},
        503: {"description": "Vector store or embedding provider unavailable"},
    },
)
def retrieve_chunks(
    payload: RagRetrieveRequest, service: RagRetrievalServiceDep
) -> RagRetrieveResponse:
    """
    Return the passages in ONE discharge document that best match a query.

    The response contains verbatim excerpts with their source page. It is not
    an answer, a summary, or clinical guidance: a low or empty result means
    the document does not cover the question, and the caller should say so.

    An empty `chunks` list is a success, not a failure.
    """
    result = service.retrieve(
        patient_id=payload.patient_id,
        document_id=payload.document_id,
        query=payload.query,
        top_k=payload.top_k,
    )
    return RagRetrieveResponse.from_result(result)


@router.post(
    "/rag/documents/{document_id}/index",
    response_model=RagIndexResponse,
    status_code=status.HTTP_200_OK,
    summary="Index a discharge document for retrieval",
    responses={
        404: {"description": "Document not found for this patient"},
        422: {
            "description": "Document is not indexable (failed or no text)"
        },
        502: {"description": "Embedding provider failed"},
        503: {"description": "Vector store or embedding provider unavailable"},
    },
)
def index_document(
    document_id: uuid.UUID,
    payload: RagIndexRequest,
    service: RagIndexingServiceDep,
) -> RagIndexResponse:
    """
    Chunk, embed, and store one document's extracted text.

    Explicit rather than automatic: uploading a document does not index it.
    That keeps the Phase 2 upload path unchanged and lets an operator re-index
    after changing chunking settings without re-uploading the file.

    Safe to call repeatedly - chunk IDs are deterministic and the previous
    generation is replaced rather than duplicated.
    """
    result = service.index_document(
        patient_id=payload.patient_id, document_id=document_id
    )
    return RagIndexResponse.model_validate(result, from_attributes=True)
