"""
CareLoop AI - Phase 4 Agent Safety Tests

Covers the independent post-validation: refusals, citation authenticity,
medical overreach (reusing Phase 2 `has_overreach`), the lexical grounding
guard, document scoping, and fail-closed behaviour when text is unavailable.

WHAT THESE TESTS DO NOT CLAIM
They do not show that grounded answers are medically correct, nor that
hallucination is impossible. They show that specific unsafe shapes are
withheld. See the module docstring of `app/agent/safety.py`.
"""
from __future__ import annotations

import uuid

import pytest

from app.agent.safety import AgentSafetyValidator
from app.agent.state import GroundedAnswer, GroundedSource, SafetyFlagKind
from app.services.safety import has_overreach

SOURCE_TEXT = (
    "Paracetamol 500 mg to be taken orally every six hours as required for "
    "pain relief. Continue for five days."
)


def _source(chunk_id="c1", document_id=None, page=1) -> GroundedSource:
    return GroundedSource(
        discharge_document_id=document_id or uuid.uuid4(),
        chunk_id=chunk_id,
        source_page=page,
        score=0.9,
    )


def _draft(**overrides) -> GroundedAnswer:
    base = dict(
        answer=(
            "Paracetamol 500 mg to be taken orally every six hours as required "
            "for pain relief. Continue for five days."
        ),
        supported=True,
        cited_chunk_ids=["c1"],
    )
    base.update(overrides)
    return GroundedAnswer(**base)


_UNSET = object()


def _validate(draft, sources, retrieved=("c1",), text=_UNSET, validator=None):
    validator = validator or AgentSafetyValidator()
    return validator.validate(
        draft=draft,
        sources=sources,
        retrieved_chunk_ids=set(retrieved),
        # A distinct sentinel, so `text=None` can be tested as a real
        # "nothing available" case rather than collapsing into the default.
        chunk_text_by_id=(
            {"c1": SOURCE_TEXT} if text is _UNSET else text
        ),
    )


# ── Happy path ───────────────────────────────────────────────────────────────


def test_well_grounded_answer_passes():
    result = _validate(_draft(), [_source()])
    assert result.safe is True
    assert result.flags == []


def test_pass_requires_no_review_flag():
    assert _validate(_draft(), [_source()]).flags == []


# ── Refusals are allowed, and are not treated as failures ────────────────────


def test_model_declining_is_a_safe_outcome():
    """
    Reporting "not in the document" is the correct behaviour, not a failure.

    If this were flagged, the system would be pushing the model toward always
    answering, which is the opposite of what grounding requires.
    """
    result = _validate(
        _draft(answer=None, supported=False, cited_chunk_ids=[]), []
    )
    assert result.safe is True
    assert result.flags == []


def test_supported_claim_with_no_answer_is_rejected():
    """The model cannot claim support while providing nothing."""
    result = _validate(_draft(answer=None, supported=True), [_source()])
    assert result.safe is False
    assert SafetyFlagKind.UNSUPPORTED_BY_SOURCES in result.flags


def test_whitespace_answer_is_treated_as_absent():
    result = _validate(_draft(answer="   ", supported=True), [_source()])
    assert result.safe is False


# ── Citation authenticity ────────────────────────────────────────────────────


def test_fabricated_citation_is_rejected_and_withheld():
    """
    A citation the model invented is the most serious failure mode.

    `c999` was never retrieved, so the answer is withheld outright rather than
    shown with a source that does not exist.
    """
    result = _validate(_draft(cited_chunk_ids=["c999"]), [_source()])
    assert result.safe is False
    assert SafetyFlagKind.FABRICATED_SOURCE in result.flags


def test_partly_fabricated_citation_is_rejected():
    """One invented id among real ones still fails the whole answer."""
    result = _validate(_draft(cited_chunk_ids=["c1", "c999"]), [_source()])
    assert result.safe is False
    assert SafetyFlagKind.FABRICATED_SOURCE in result.flags


def test_answer_without_any_citation_is_rejected():
    result = _validate(_draft(cited_chunk_ids=[]), [_source()])
    assert result.safe is False
    assert SafetyFlagKind.UNSUPPORTED_BY_SOURCES in result.flags


def test_citation_not_resolving_to_a_source_is_rejected():
    """Real id, but no source record - unreachable in normal flow, still fails."""
    result = _validate(_draft(cited_chunk_ids=["c1"]), [])
    assert result.safe is False
    assert SafetyFlagKind.FABRICATED_SOURCE in result.flags


def test_none_draft_is_rejected():
    """No usable model output at all."""
    result = _validate(None, [_source()])
    assert result.safe is False
    assert SafetyFlagKind.INVALID_MODEL_OUTPUT in result.flags


# ── Document scoping (defence in depth) ──────────────────────────────────────


def test_sources_spanning_two_documents_are_rejected():
    a, b = uuid.uuid4(), uuid.uuid4()
    result = _validate(
        _draft(cited_chunk_ids=["c1", "c2"]),
        [_source("c1", a), _source("c2", b)],
        retrieved=("c1", "c2"),
        text={"c1": SOURCE_TEXT, "c2": SOURCE_TEXT},
    )
    assert result.safe is False
    assert SafetyFlagKind.FABRICATED_SOURCE in result.flags


# ── Medical overreach (Phase 2 rules reused) ─────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "You should take 2 tablets of Paracetamol 500 mg every four hours.",
        "I recommend increasing your dose to 1000 mg.",
        "You likely have a postoperative infection.",
        "This means that you have developed pneumonia.",
        "You should stop taking the Lisinopril immediately.",
        "This is an emergency, seek emergency care now.",
        "You can safely skip the evening dose.",
    ],
)
def test_medical_overreach_is_rejected(text):
    """
    Guidance, dosage change, diagnosis, and emergency phrasing are withheld.

    The answer is never rewritten: correcting clinical wording is itself a
    medical judgement this system does not make.
    """
    result = _validate(
        _draft(answer=text, cited_chunk_ids=["c1"]), [_source()]
    )
    assert result.safe is False
    assert SafetyFlagKind.MEDICAL_OVERREACH in result.flags


def test_overreach_detection_reuses_phase2_has_overreach():
    """
    Phase 2's `has_overreach` is genuinely wired in, not reimplemented.

    A phrase matched ONLY by a Phase 2 pattern must be caught, proving the
    existing logic is the source of truth rather than a lookalike. "diagnosed
    with" is a Phase 2 pattern that the agent's own patterns do not cover.
    """
    phase2_only = "The patient was diagnosed with pneumonia before admission."
    assert has_overreach(phase2_only) is True
    result = _validate(_draft(answer=phase2_only), [_source()])
    assert result.safe is False
    assert SafetyFlagKind.MEDICAL_OVERREACH in result.flags


def test_reported_dosage_from_the_document_is_allowed():
    """
    Quoting a dose the document actually states is the intended behaviour.

    If this were rejected, the system could not answer dosage questions at all.
    """
    text = (
        "Paracetamol 500 mg to be taken orally every six hours as required for "
        "pain relief. Continue for five days."
    )
    result = _validate(_draft(answer=text), [_source()])
    assert result.safe is True


# ── Lexical grounding guard ──────────────────────────────────────────────────


def test_answer_from_unrelated_vocabulary_fails_closed():
    """
    An answer sharing almost nothing with its cited passage is withheld.

    This is the guard's value: the model produced fluent text unrelated to what
    was retrieved, and overlap is what catches it without a second LLM call.
    """
    invented = (
        "The patient should undertake aquatic aerobics three times weekly and "
        "consume a probiotic supplement containing Lactobacillus strains for "
        "optimal gastrointestinal restoration."
    )
    result = _validate(_draft(answer=invented), [_source()])
    assert result.safe is False
    assert SafetyFlagKind.GROUNDING_NOT_ESTABLISHED in result.flags


def test_missing_source_text_fails_closed():
    """
    Grounding that cannot be positively established is not established.

    The check must not treat "I could not measure it" as "it passed".
    """
    result = _validate(_draft(), [_source()], text={})
    assert result.safe is False
    assert SafetyFlagKind.GROUNDING_NOT_ESTABLISHED in result.flags


def test_none_text_mapping_fails_closed():
    result = _validate(_draft(), [_source()], text=None)
    assert result.safe is False
    assert SafetyFlagKind.GROUNDING_NOT_ESTABLISHED in result.flags


def test_overlap_threshold_is_configurable():
    """
    A stricter configured threshold must reject an answer the default allows.

    Uses a paraphrase whose overlap is below 1.0 but above the default, so the
    only thing that can flip the result is the configured value. This proves
    the setting is read rather than hardcoded.
    """
    from app.core.config import get_settings

    paraphrase = (
        "Take Paracetamol 500 mg orally every six hours for pain relief, and "
        "continue this for five days."
    )
    draft = _draft(answer=paraphrase)

    # Default threshold allows ordinary summarising.
    assert _validate(draft, [_source()]).safe is True

    settings = get_settings().model_copy(
        update={"agent_min_overlap_ratio": 1.0}
    )
    validator = AgentSafetyValidator(settings=settings)
    assert validator.min_overlap_ratio == 1.0
    # 1.0 is unreachable for a paraphrase, so this must now fail.
    assert _validate(draft, [_source()], validator=validator).safe is False


def test_default_threshold_is_permissive_enough_for_paraphrase():
    """The guard must not be so strict that ordinary summarising fails."""
    paraphrase = (
        "Take Paracetamol 500 mg orally every six hours for pain relief, and "
        "continue this for five days."
    )
    result = _validate(_draft(answer=paraphrase), [_source()])
    assert result.safe is True


# ── Statelessness ────────────────────────────────────────────────────────────


def test_validation_is_stateless():
    """Repeated validation of the same input gives the same result."""
    validator = AgentSafetyValidator()
    draft, source = _draft(), [_source()]
    first = validator.validate(
        draft=draft, sources=source, retrieved_chunk_ids={"c1"},
        chunk_text_by_id={"c1": SOURCE_TEXT},
    )
    second = validator.validate(
        draft=draft, sources=source, retrieved_chunk_ids={"c1"},
        chunk_text_by_id={"c1": SOURCE_TEXT},
    )
    assert first.safe == second.safe
    assert first.flags == second.flags


def test_assessment_flag_values_are_strings():
    """The API surface is machine-readable strings, not enums."""
    result = _validate(_draft(cited_chunk_ids=["c999"]), [_source()])
    assert all(isinstance(v, str) for v in result.flag_values)
    assert "fabricated_source" in result.flag_values
