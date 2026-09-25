"""
CareLoop AI — Extraction Safety Validation (Phase 2)

Independent post-validation of LLM output.  The extraction prompt asks the
model to behave safely; this module ASSUMES the model may not have complied
and independently enforces the safety boundary.

What is enforced here:
  - no item may claim a source page outside the document
  - an item with a required-but-missing identifying value is dropped rather
    than persisted as a half-record
  - dosage / frequency are never synthesised: if absent they stay absent
  - clinical text is never rewritten; suspicious over-reach phrasing is
    detected and flagged for review instead of being trusted
  - every warning symptom is marked needs_review

What this module does NOT do:
  - diagnose, triage, or assess clinical risk
  - decide whether a dose is appropriate
  - modify or "correct" any extracted value

A failed safety check never persists partial data: the caller aborts the
transaction.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from app.core.logging import get_logger
from app.schemas.extraction import (
    ExtractedAppointment,
    ExtractedMedication,
    ExtractedWarningSymptom,
    StructuredDischargeExtraction,
)

logger = get_logger(__name__)

# Phrasing that indicates the model invented clinical judgement rather than
# transcribing.  Matches are flagged, never rewritten.
_OVERREACH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bI (?:recommend|suggest|advise)\b", re.IGNORECASE),
    re.compile(r"\b(?:you|patient) should (?:take|start|stop|increase|decrease)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:diagnosis|diagnosed with|prognosis)\b", re.IGNORECASE),
    re.compile(r"\b(?:is (?:an )?emergency|call 911|seek emergency care)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:increase|reduce) the dose\b", re.IGNORECASE),
)

# Placeholder values an LLM sometimes emits instead of null.  They are
# treated as "missing" so they are never persisted as real clinical data.
_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        "n/a",
        "na",
        "none",
        "null",
        "nil",
        "unknown",
        "not specified",
        "not provided",
        "not mentioned",
        "not available",
        "not stated",
        "tbd",
    }
)


def is_placeholder(value: str | None) -> bool:
    """True when a value is effectively absent."""
    if value is None:
        return True
    return value.strip().lower() in _PLACEHOLDER_VALUES


def has_overreach(text: str | None) -> bool:
    """True when text contains model-authored clinical guidance."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _OVERREACH_PATTERNS)


class SafetyValidator:
    """
    Validates and sanitises a structured extraction.

    The validator is intentionally conservative: when in doubt an item is
    kept but marked ``needs_review`` rather than being dropped, EXCEPT when
    the item has no usable identifying value at all, in which case it is
    dropped because persisting it would create a meaningless record.
    """

    def __init__(self, page_count: int | None = None) -> None:
        self._page_count = page_count

    # ── Public API ──────────────────────────────────────────────────────────

    def validate(
        self, extraction: StructuredDischargeExtraction
    ) -> Tuple[
        StructuredDischargeExtraction, List[str]
    ]:
        """
        Return ``(sanitised_extraction, notes)``.

        ``notes`` are human-readable audit strings describing every
        adjustment made, suitable for the extraction audit record.
        """
        notes: list[str] = []

        medications = self._validate_medications(extraction.medications, notes)
        appointments = self._validate_appointments(extraction.appointments, notes)
        symptoms = self._validate_warning_symptoms(
            extraction.warning_symptoms, notes
        )

        sanitised = StructuredDischargeExtraction(
            patient_name=self._clean_scalar(extraction.patient_name),
            hospital_name=self._clean_scalar(extraction.hospital_name),
            admission_date=self._clean_scalar(extraction.admission_date),
            discharge_date=self._clean_scalar(extraction.discharge_date),
            medications=medications,
            appointments=appointments,
            warning_symptoms=symptoms,
        )
        return sanitised, notes

    def count_needing_review(
        self, extraction: StructuredDischargeExtraction
    ) -> int:
        """Total items across all categories flagged for human review."""
        return (
            sum(1 for m in extraction.medications if m.needs_review)
            + sum(1 for a in extraction.appointments if a.needs_review)
            + sum(1 for w in extraction.warning_symptoms if w.needs_review)
        )

    # ── Category validators ─────────────────────────────────────────────────

    def _validate_medications(
        self, items: List[ExtractedMedication], notes: list[str]
    ) -> List[ExtractedMedication]:
        kept: list[ExtractedMedication] = []
        for index, item in enumerate(items):
            if is_placeholder(item.medication_name):
                notes.append(
                    f"medications[{index}] dropped: no medication name present"
                )
                continue

            cleaned = item.model_copy(deep=True)
            self._normalise_scalars(cleaned, notes, index, "medications")
            self._check_page(cleaned, notes, index, "medications")
            self._check_overreach(cleaned, notes, index, "medications")
            kept.append(cleaned)
        return kept

    def _validate_appointments(
        self, items: List[ExtractedAppointment], notes: list[str]
    ) -> List[ExtractedAppointment]:
        kept: list[ExtractedAppointment] = []
        for index, item in enumerate(items):
            # An appointment needs at least one identifying attribute.
            if all(
                is_placeholder(value)
                for value in (
                    item.appointment_type,
                    item.doctor_or_department,
                    item.appointment_date,
                    item.appointment_time,
                    item.location,
                )
            ):
                notes.append(
                    f"appointments[{index}] dropped: no identifying information"
                )
                continue

            cleaned = item.model_copy(deep=True)
            self._normalise_scalars(cleaned, notes, index, "appointments")
            self._check_page(cleaned, notes, index, "appointments")
            self._check_overreach(cleaned, notes, index, "appointments")
            kept.append(cleaned)
        return kept

    def _validate_warning_symptoms(
        self, items: List[ExtractedWarningSymptom], notes: list[str]
    ) -> List[ExtractedWarningSymptom]:
        kept: list[ExtractedWarningSymptom] = []
        for index, item in enumerate(items):
            if is_placeholder(item.symptom) and is_placeholder(item.instruction):
                notes.append(
                    f"warning_symptoms[{index}] dropped: no symptom or instruction"
                )
                continue

            cleaned = item.model_copy(deep=True)
            # Warning symptoms are ALWAYS pending human confirmation.
            cleaned.needs_review = True
            self._normalise_scalars(cleaned, notes, index, "warning_symptoms")
            self._check_page(cleaned, notes, index, "warning_symptoms")
            self._check_overreach(cleaned, notes, index, "warning_symptoms")
            kept.append(cleaned)
        return kept

    # ── Shared field handling ───────────────────────────────────────────────

    @staticmethod
    def _clean_scalar(value: str | None) -> str | None:
        """Convert placeholder strings to a real None; leave real text alone."""
        if is_placeholder(value):
            return None
        return value.strip()

    def _normalise_scalars(
        self, item, notes: list[str], index: int, category: str
    ) -> None:
        """
        Replace placeholder strings with None across an item's string fields.

        This is what stops "N/A" or "not specified" from being persisted as
        if the clinician had written it.
        """
        for field_name, value in list(type(item).model_fields.items()):
            if field_name in ("source_page", "needs_review"):
                continue
            current = getattr(item, field_name, None)
            if isinstance(current, str) and is_placeholder(current):
                setattr(item, field_name, None)
                notes.append(
                    f"{category}[{index}].{field_name} treated as missing"
                )

        # A medication with a name but no dosage/frequency at all is
        # incomplete: keep it, but require review.  Dosage is NEVER invented.
        if category == "medications":
            if is_placeholder(item.dosage) or is_placeholder(item.frequency):
                if not item.needs_review:
                    item.needs_review = True
                    notes.append(
                        f"{category}[{index}] needs_review: dosage or frequency "
                        "absent; value left null rather than inferred"
                    )

    def _check_page(
        self, item, notes: list[str], index: int, category: str
    ) -> None:
        """Null an impossible source_page rather than storing a bad citation."""
        page = getattr(item, "source_page", None)
        if page is None:
            return
        if self._page_count is not None and page > self._page_count:
            notes.append(
                f"{category}[{index}].source_page {page} exceeds document "
                f"page count {self._page_count}; cleared"
            )
            item.source_page = None
            item.needs_review = True
        if page < 1:
            item.source_page = None

    def _check_overreach(
        self, item, notes: list[str], index: int, category: str
    ) -> None:
        """
        Flag items where the model appears to have authored clinical advice.

        The text is NOT rewritten (that would alter clinical content); the
        item is marked for review and the note records the concern.
        """
        for field_name in type(item).model_fields:
            current = getattr(item, field_name, None)
            if isinstance(current, str) and has_overreach(current):
                if not item.needs_review:
                    item.needs_review = True
                notes.append(
                    f"{category}[{index}].{field_name} contains model-authored "
                    "clinical guidance; flagged for review without rewriting"
                )
