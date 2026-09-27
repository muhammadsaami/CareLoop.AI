"""
CareLoop AI — API Dependencies
"""
from typing import Annotated, Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.agent.generation import GroundedResponseGenerator
from app.agent.safety import AgentSafetyValidator
from app.agent.service import AgentService
from app.core.database import get_db
from app.llm.base import LLMProvider
from app.rag.indexing import RagIndexingService
from app.rag.retrieval import RagRetrievalService
from app.services.discharge_document import DischargeDocumentService
from app.services.extraction import ExtractionService

# Typed alias for injection — use this in route function signatures
DbSession = Annotated[Session, Depends(get_db)]


def get_llm_provider() -> Iterator[LLMProvider | None]:
    """
    Yield the configured extraction provider, or None when unavailable.

    Returning None lets `ExtractionService` resolve the provider itself and
    raise a precise, actionable configuration error at extraction time
    rather than failing an otherwise valid upload.  Exposed as a dependency
    so tests can substitute a provider without any network access.
    """
    yield None


LLMProviderDep = Annotated[LLMProvider | None, Depends(get_llm_provider)]


def get_discharge_document_service(
    db: DbSession,
    provider: LLMProviderDep,
) -> Iterator[DischargeDocumentService]:
    """Build the discharge document service for one request."""
    yield DischargeDocumentService(db, llm_provider=provider)


DischargeDocumentServiceDep = Annotated[
    DischargeDocumentService, Depends(get_discharge_document_service)
]

# ── Phase 3: RAG ─────────────────────────────────────────────────────────────
# Both services are built per request so they share that request's database
# session.  The vector store resolves its embedding provider lazily from
# settings, which keeps this module free of any RAG import cycle and lets
# tests inject a store by overriding the dependency.


def get_rag_indexing_service(db: DbSession) -> Iterator[RagIndexingService]:
    """Build the RAG indexing service for one request."""
    yield RagIndexingService(db)


def get_rag_retrieval_service(db: DbSession) -> Iterator[RagRetrievalService]:
    """Build the RAG retrieval service for one request."""
    yield RagRetrievalService(db)


RagIndexingServiceDep = Annotated[
    RagIndexingService, Depends(get_rag_indexing_service)
]
RagRetrievalServiceDep = Annotated[
    RagRetrievalService, Depends(get_rag_retrieval_service)
]

# ── Phase 4: LangGraph agent ────────────────────────────────────────────────
# The agent is assembled from the SAME retrieval dependency and the SAME
# provider seam Phase 2/3 use.  There is deliberately no agent-specific
# retrieval or provider path: reusing these is what guarantees the agent
# inherits Phase 3's patient/document scoping and fingerprint validation
# instead of re-implementing it.


def get_agent_safety_validator() -> Iterator[AgentSafetyValidator]:
    """Build the independent safety validator for one request."""
    yield AgentSafetyValidator()


AgentSafetyValidatorDep = Annotated[
    AgentSafetyValidator, Depends(get_agent_safety_validator)
]


def get_grounded_response_generator(
    provider: LLMProviderDep,
) -> Iterator[GroundedResponseGenerator]:
    """
    Build the grounded-answer generator for one request.

    Takes the same `LLMProviderDep` as `DischargeDocumentService`, so a test
    override of the provider reaches the agent with no separate wiring and no
    second provider is ever constructed.
    """
    yield GroundedResponseGenerator(llm_provider=provider)


GroundedResponseGeneratorDep = Annotated[
    GroundedResponseGenerator, Depends(get_grounded_response_generator)
]


def get_agent_service(
    retrieval: RagRetrievalServiceDep,
    generator: GroundedResponseGeneratorDep,
    validator: AgentSafetyValidatorDep,
) -> Iterator[AgentService]:
    """
    Build the agent for one request.

    Per request so the graph shares that request's database session through the
    retrieval service.  The graph itself holds no per-patient state, so no
    cross-request leakage is possible.
    """
    yield AgentService(
        retrieval=retrieval,
        generator=generator,
        validator=validator,
    )


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]

# Re-exported so tests and future routes share a single construction path.
__all__ = [
    "AgentSafetyValidatorDep",
    "AgentServiceDep",
    "DbSession",
    "GroundedResponseGeneratorDep",
    "LLMProviderDep",
    "DischargeDocumentServiceDep",
    "RagIndexingServiceDep",
    "RagRetrievalServiceDep",
    "get_agent_safety_validator",
    "get_agent_service",
    "get_grounded_response_generator",
    "get_llm_provider",
    "get_discharge_document_service",
    "get_rag_indexing_service",
    "get_rag_retrieval_service",
    "ExtractionService",
]
