"""
CareLoop AI - Agent Safety Validation (Phase 4)

Independent post-validation of the model's answer.  The prompt asks the model
to behave; this module ASSUMES it did not and re-checks.  This mirrors Phase 2
exactly: `services/safety.py` does not trust the extraction prompt, and neither
does this trust the grounded-answer prompt.

WHAT THIS IS, PRECISELY
A DETERMINISTIC SAFETY GUARD.  It checks structure, citation, and phrasing.

WHAT THIS IS NOT
It is NOT proof that an answer is medically correct.
It is NOT semantic entailment - it does not verify that the sources actually
assert what the answer claims, only that the answer's vocabulary is drawn from
them.
It does NOT make hallucination impossible.  A fluent, confidently-worded
statement that reuses vocabulary from a real cited passage could pass these
checks.  That is exactly why every answer produced here carries
`needs_review` unless it is fully source-anchored, and why the system never
presents a grounded answer as clinical advice.

FAIL CLOSED
Any doubt resolves to a flag plus `needs_review=True`.  Nothing is rewritten
and no clinical text is edited: a suspicious answer is withheld, not "fixed",
because correcting clinical wording is itself a medical judgement.

NOT IN SCOPE
  * diagnosing, triaging, or assessing clinical risk
  * judging whether a documented dose is appropriate
  * emergency detection or escalation
  * a second LLM call to cross-check the first
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Set

from app.agent.state import GroundedAnswer, GroundedSource, SafetyFlagKind
from app.core.config import Settings
from app.core.logging import get_logger
from app.services.safety import has_overreach, is_placeholder

logger = get_logger(__name__)

#: Phrasing that indicates the model authored guidance or asserted a clinical
#: conclusion rather than reporting the document.  Reuses the Phase 2
#: `has_overreach()` vocabulary and adds agent-specific cases.
_AGENT_OVERREACH_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Asserting a diagnosis in the first person / about the reader.
    re.compile(r"\byou (?:have|are suffering from|likely have)\b", re.IGNORECASE),
    re.compile(r"\bthis (?:means|indicates|suggests) that you\b", re.IGNORECASE),
    # Emergency or triage judgement.
    re.compile(
        r"\b(?:seek emergency|go to the ER|call an ambulance|this is an emergency)\b",
        re.IGNORECASE,
    ),
    # Instruction to alter a medication.
    re.compile(
        r"\b(?:you should|you must|you can safely) (?:take|start|stop|skip|"
        r"increase|reduce|halve|double)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:adjust|change|modify) (?:your )?(?:dose|dosage|medication)\b",
               re.IGNORECASE),
    # Calculated or converted dosing.
    re.compile(r"\b(?:take|use) \d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b(?!\s*(?:as|to|for|every|twice|once))",
               re.IGNORECASE),
    # Clinical recommendation verbs.
    re.compile(r"\b(?:I|we) (?:recommend|suggest|advise|urge)\b", re.IGNORECASE),
    re.compile(r"\byou should (?:rest|avoid|stop|start|begin|continue)\b",
               re.IGNORECASE),
)

#: Words too common to carry evidential weight in an overlap check.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "than", "so",
        "of", "to", "in", "on", "at", "by", "for", "with", "from", "as",
        "is", "are", "was", "were", "be", "been", "being", "it", "its",
        "this", "that", "these", "those", "there", "here", "what", "which",
        "who", "whom", "when", "where", "why", "how", "do", "does", "did",
        "can", "could", "should", "would", "may", "might", "must", "will",
        "i", "you", "he", "she", "we", "they", "my", "your", "his", "her",
        "our", "their", "me", "us", "them", "not", "no", "yes", "any", "all",
        "some", "each", "more", "most", "other", "such", "only", "own", "same",
        "about", "into", "over", "after", "before", "between", "under", "again",
        "list", "document", "summary", "discharge", "patient",
    }
)

#: Fallback grounding threshold used when no Settings instance is injected.
#: The effective value is `settings.agent_min_overlap_ratio`.
_DEFAULT_MIN_OVERLAP_RATIO = 0.30


@dataclass(frozen=True)
class SafetyAssessment:
    """Outcome of validating one draft answer."""

    safe: bool
    flags: List[SafetyFlagKind] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def flag_values(self) -> List[str]:
        return [flag.value for flag in self.flags]


class AgentSafetyValidator:
    """
    Validates a schema-valid draft against the evidence that was retrieved.

    Stateless: every call is judged only on its own arguments.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings

    @property
    def min_overlap_ratio(self) -> float:
        """
        Required share of a cited chunk's distinctive vocabulary in the answer.

        Tuned to be permissive about paraphrase while still failing an answer
        built from vocabulary absent from its sources.  See the module
        docstring: this is a guard, not entailment.
        """
        if self._settings is not None:
            return float(
                getattr(
                    self._settings,
                    "agent_min_overlap_ratio",
                    _DEFAULT_MIN_OVERLAP_RATIO,
                )
            )
        return _DEFAULT_MIN_OVERLAP_RATIO

    def validate(
        self,
        *,
        draft: Optional[GroundedAnswer],
        sources: List[GroundedSource],
        retrieved_chunk_ids: Set[str],
        chunk_text_by_id: Optional[Mapping[str, str]] = None,
    ) -> SafetyAssessment:
        """
        Return a `SafetyAssessment` for one draft.

        `retrieved_chunk_ids` is the set of chunk ids that actually came back
        from Phase 3 retrieval, which is what makes a fabricated citation
        detectable.

        `chunk_text_by_id` maps retrieved chunk id to its source text, and is
        used only for the lexical-overlap guard.  It is passed in separately
        rather than stored on `GroundedSource` so that source text never
        becomes part of the graph state - the state stays loggable, and
        `AgentState.to_loggable()` can never accidentally serialise clinical
        text.
        """
        flags: list[SafetyFlagKind] = []
        notes: list[str] = []

        # A missing draft means the provider was unusable. There is nothing to
        # assess, so nothing is asserted.
        if draft is None:
            return SafetyAssessment(
                safe=False,
                flags=[SafetyFlagKind.INVALID_MODEL_OUTPUT],
                notes=["provider returned no usable object"],
            )

        # ── 1. A claim of support with nothing to show is contradictory ─
        if draft.supported and draft.answer is None:
            flags.append(SafetyFlagKind.UNSUPPORTED_BY_SOURCES)
            notes.append("model claimed supported but returned no answer")
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        # ── 2. The model declined ─────────────────────────────────────
        # Not a safety failure: reporting "not in the document" is the correct
        # outcome and must be allowed through as a refusal. Penalising it would
        # pressure the model toward always answering, which is the opposite of
        # what grounding requires.
        if not draft.supported or draft.answer is None:
            return SafetyAssessment(
                safe=True,
                flags=flags,
                notes=notes + ["model declined; no answer asserted"],
            )

        # ── 1. An answer must actually be present ──────────────────────
        if is_placeholder(draft.answer):
            flags.append(SafetyFlagKind.UNSUPPORTED_BY_SOURCES)
            notes.append("answer was empty or a placeholder")
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        # ── 2. Citations must be real ──────────────────────────────────
        # Any cited id that was not retrieved is a fabricated source, which is
        # the most serious failure this node exists to catch.
        unknown = [
            cid for cid in draft.cited_chunk_ids if cid not in retrieved_chunk_ids
        ]
        if unknown:
            flags.append(SafetyFlagKind.FABRICATED_SOURCE)
            notes.append(
                f"{len(unknown)} cited chunk id(s) were not present in the "
                f"retrieved context; answer withheld"
            )
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        if not draft.cited_chunk_ids:
            flags.append(SafetyFlagKind.UNSUPPORTED_BY_SOURCES)
            notes.append("answer asserted without citing any source")
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        if not sources:
            # The ids were real but none resolved to a source record.  This
            # should be unreachable, and is treated as a hard failure rather
            # than assumed benign.
            flags.append(SafetyFlagKind.FABRICATED_SOURCE)
            notes.append("cited ids did not resolve to any source record")
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        # ── 3. Every source must belong to the requested document ──────
        # Defence in depth: retrieval already scopes by document, so a source
        # from elsewhere means a boundary was crossed somewhere upstream.
        document_ids = {source.discharge_document_id for source in sources}
        if len(document_ids) > 1:
            flags.append(SafetyFlagKind.FABRICATED_SOURCE)
            notes.append("sources span more than one document")
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        # ── 4. Medical overreach in the wording ────────────────────────
        if has_overreach(draft.answer) or self._has_agent_overreach(
            draft.answer
        ):
            flags.append(SafetyFlagKind.MEDICAL_OVERREACH)
            notes.append(
                "answer contains guidance-style phrasing; withheld without "
                "rewriting"
            )
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        # ── 5. Lexical grounding ───────────────────────────────────────
        # A GUARD, not entailment. It confirms the answer is drawn from the
        # vocabulary of the passages it cites; it cannot confirm the passages
        # actually assert the answer's claim.
        overlap = self._min_overlap(draft.answer, sources, chunk_text_by_id)
        if overlap is None:
            # Text was unavailable, so grounding could not be positively
            # established. Fail closed.
            flags.append(SafetyFlagKind.GROUNDING_NOT_ESTABLISHED)
            notes.append(
                "source text unavailable for grounding check; failing closed"
            )
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        if overlap < self.min_overlap_ratio:
            flags.append(SafetyFlagKind.GROUNDING_NOT_ESTABLISHED)
            notes.append(
                f"answer shared insufficient vocabulary with its sources "
                f"(overlap={overlap:.2f}); withholding rather than asserting"
            )
            return SafetyAssessment(safe=False, flags=flags, notes=notes)

        return SafetyAssessment(
            safe=True,
            flags=flags,
            notes=notes + ["answer is anchored to retrieved sources"],
        )

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _has_agent_overreach(text: str) -> bool:
        """Agent-specific overreach on top of the Phase 2 patterns."""
        return any(pattern.search(text) for pattern in _AGENT_OVERREACH_PATTERNS)

    @staticmethod
    def _min_overlap(
        answer: str,
        sources: List[GroundedSource],
        chunk_text_by_id: Optional[Mapping[str, str]] = None,
    ) -> Optional[float]:
        """
        Smallest lexical overlap between the answer and any single source.

        Returns None when overlap cannot be computed, which the caller treats
        as "grounding not established" rather than "fine".  The `min` across
        sources is deliberate: an answer must be anchored to EVERY source it
        cites, not merely to one of them.
        """
        answer_tokens = AgentSafetyValidator._content_tokens(answer)
        if not answer_tokens or not chunk_text_by_id:
            return None

        ratios: list[float] = []
        for source in sources:
            source_text = chunk_text_by_id.get(source.chunk_id)
            if source_text is None:
                continue
            source_tokens = AgentSafetyValidator._content_tokens(source_text)
            if not source_tokens:
                continue
            shared = answer_tokens & source_tokens
            ratios.append(len(shared) / len(answer_tokens))

        return min(ratios) if ratios else None

    @staticmethod
    def _content_tokens(text: str) -> Set[str]:
        """Lowercase word tokens of at least 4 characters, stopwords removed."""
        return {
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) >= 4 and token not in _STOPWORDS
        }


__all__ = ["AgentSafetyValidator", "SafetyAssessment"]
