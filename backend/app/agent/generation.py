"""
CareLoop AI - Grounded Response Generation (Phase 4)

Calls the EXISTING Phase 2 LLM abstraction (`LLMProvider.extract_structured`).
No Groq or Gemini SDK is imported here, no API key is read, and no second
provider is constructed: the agent reuses exactly the seam Phase 2 built.

HOW SOURCE GROUNDING IS ENFORCED STRUCTURALLY
The model returns `cited_chunk_ids` - opaque ids it was shown - and nothing
else about provenance.  This module then resolves each id against the chunks
that were ACTUALLY retrieved:

    model output            real retrieved chunk
    ------------            --------------------
    cited_chunk_ids  ----->  GroundedSource(chunk_id, source_page, score)

`source_page` is copied from the Phase 3 chunk and is never accepted from the
model.  Two consequences, both intentional:
  * a fabricated chunk id is detected (it resolves to nothing) and the answer
    is rejected rather than shown;
  * a fabricated page number is not merely discouraged, it is unrepresentable.

The model therefore cannot assert provenance.  It can only select among
provenance the system already established.
"""
from __future__ import annotations

import uuid
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.agent.prompts import build_grounded_prompt
from app.agent.state import GroundedAnswer, GroundedSource
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    MalformedProviderOutputError,
    ProviderError,
    ProviderNotConfiguredError,
)
from app.core.logging import get_logger
from app.llm.base import LLMProvider
from app.llm.factory import get_provider
from app.rag.retrieval import RetrievedChunk, RetrievalResult

logger = get_logger(__name__)

#: Name of the JSON schema envelope sent to the provider.
GROUNDED_SCHEMA_NAME = "careloop_grounded_answer"

#: Fallback prompt budget used when no Settings instance is injected.  The
#: effective limit is `settings.agent_max_prompt_chars`.
_DEFAULT_PROMPT_CHARS = 12000


class GroundedAnswerDraft(BaseModel):
    """
    The raw shape the model is asked to return.

    Kept separate from `GroundedAnswer` on purpose: this is the model's
    *claim*, validated for shape only.  Whether the claim is honest is decided
    by `agent/safety.py`.  Conflating the two would let a well-formed
    fabrication pass as a result.
    """

    model_config = ConfigDict(extra="ignore")

    answer: Optional[str] = None
    supported: bool = False
    cited_chunk_ids: List[str] = Field(default_factory=list)
    model_declined_reason: Optional[str] = None

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Provider-facing JSON schema, derived from the model itself."""
        return cls.model_json_schema()


class GroundedResponseGenerator:
    """
    Turns retrieved chunks into a schema-validated, source-anchored draft.

    Stateless and per-request: it holds no conversation history, so one
    request can never influence another.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = llm_provider

    # ── Provider access ─────────────────────────────────────────────────────

    @property
    def provider(self) -> LLMProvider:
        """
        Resolve the configured Phase 2 provider lazily.

        Deferred for the same reason `ExtractionService` defers it: a missing
        key must surface as a precise, actionable error at the moment an
        answer is actually needed, not when the module is imported.
        """
        if self._provider is None:
            self._provider = get_provider(settings=self._settings)
        return self._provider

    # ── Main entry point ────────────────────────────────────────────────────

    def generate(
        self,
        *,
        user_query: str,
        chunks: List[RetrievedChunk],
        document_id: uuid.UUID,
    ) -> tuple[Optional[GroundedAnswer], List[GroundedSource], str, str]:
        """
        Return ``(answer_draft, resolved_sources, provider_name, model)``.

        `answer_draft` is None when the provider is unavailable or returned
        something unusable; the caller routes that to the safe fallback rather
        than surfacing model output it cannot trust.

        `resolved_sources` always comes from real chunk metadata.
        """
        provider = self.provider
        provider_name = provider.name
        model = provider.model

        if not provider.is_configured():
            # Surfaced as a domain error, consistent with Phase 2, so the API
            # returns an actionable message instead of a silent refusal.
            raise ProviderNotConfiguredError(
                f"The '{provider_name}' provider is not configured, so the "
                "agent cannot generate a grounded answer. Retrieval still "
                "works without it.",
                internal_detail=f"provider={provider_name} is_configured=False",
            )

        system_prompt, user_prompt = build_grounded_prompt(
            user_query=user_query,
            passages=self._render_passages(
                chunks, budget=self._prompt_budget()
            ),
        )

        try:
            raw = provider.extract_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=GroundedAnswerDraft.json_schema(),
                schema_name=GROUNDED_SCHEMA_NAME,
            )
        except (ProviderError, MalformedProviderOutputError):
            # Provider failures are already domain exceptions carrying a
            # client-safe message; the traceback and vendor payload stay in
            # `internal_detail`. Re-raise so the API maps them properly.
            raise

        draft = self._coerce(raw)
        sources = self._resolve_sources(draft, chunks, document_id)
        return draft, sources, provider_name, model

    # ── Internals ───────────────────────────────────────────────────────────

    def _prompt_budget(self) -> int:
        """Source-text budget for one prompt, from settings."""
        return int(
            getattr(
                self._settings,
                "agent_max_prompt_chars",
                _DEFAULT_PROMPT_CHARS,
            )
        )

    @staticmethod
    def _render_passages(
        chunks: List[RetrievedChunk], *, budget: int
    ) -> str:
        """
        Render retrieved chunks for the prompt, labelled with their real id.

        The model is shown the exact identifiers it may cite, and nothing that
        would let it infer or invent a page number.  `budget` bounds the token
        cost of a single request; retrieval already caps chunk size and count.
        """
        blocks: list[str] = []
        for chunk in chunks:
            if budget <= 0:
                break
            text = chunk.text
            if len(text) > budget:
                text = text[:budget]
            budget -= len(text)
            location = (
                f"page {chunk.source_page}"
                if chunk.source_page is not None
                else "page unknown"
            )
            blocks.append(
                f"[chunk_id: {chunk.chunk_id} | {location}]\n{text}"
            )
        return "\n\n".join(blocks) if blocks else "(no passages available)"

    @staticmethod
    def _coerce(raw: dict[str, Any]) -> Optional[GroundedAnswer]:
        """
        Validate the provider payload into a `GroundedAnswer`.

        Returns None when the payload is not a usable object.  The caller then
        takes the safe path; an unparseable model response is never passed
        downstream as if it were content.
        """
        if not isinstance(raw, dict):
            return None
        try:
            draft = GroundedAnswerDraft.model_validate(raw)
        except Exception:  # noqa: BLE001 - any validation failure is unusable
            return None
        return GroundedAnswer(
            answer=draft.answer,
            supported=bool(draft.supported),
            cited_chunk_ids=[str(cid) for cid in draft.cited_chunk_ids],
            model_declined_reason=draft.model_declined_reason,
        )

    @staticmethod
    def _resolve_sources(
        draft: GroundedAnswer,
        chunks: List[RetrievedChunk],
        document_id: uuid.UUID,
    ) -> List[GroundedSource]:
        """
        Map the model's cited ids onto the chunks actually retrieved.

        A null page stays null: the model is never asked for a page, and an
        absent marker is reported as unknown rather than defaulted.

        An id that matches nothing is silently dropped here; the *detection* of
        that fabrication is `agent/safety.py`'s job, which compares the cited
        ids against the retrieved set.  Provenance in the returned list is
        always real.
        """
        if draft is None:
            return []

        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        resolved: list[GroundedSource] = []
        seen: set[str] = set()

        for chunk_id in draft.cited_chunk_ids:
            chunk: Optional[RetrievedChunk] = by_id.get(chunk_id)
            if chunk is None or chunk_id in seen:
                continue
            seen.add(chunk_id)
            resolved.append(
                GroundedSource(
                    discharge_document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    source_page=chunk.source_page,
                    score=chunk.score,
                )
            )
        return resolved


__all__ = [
    "GROUNDED_SCHEMA_NAME",
    "GroundedAnswerDraft",
    "GroundedResponseGenerator",
]
