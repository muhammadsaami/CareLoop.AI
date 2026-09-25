"""
CareLoop AI — Structured Extraction Schemas (Phase 2)

These Pydantic models are BOTH:
  1. the JSON schema handed to the LLM provider, and
  2. the validation gate applied to the provider's response.

Healthcare safety boundary (enforced here and in services/safety.py):
- Every field except `medication_name` / `symptom` is Optional and defaults
  to None.  Missing information MUST stay null — it is never guessed.
- `needs_review` marks anything ambiguous, partial, or uncertain.
- This is transcription of what a clinician already wrote.  It is not
  diagnosis, not prescribing, and not treatment advice.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtractedMedication(BaseModel):
    """A medication exactly as written on the discharge document."""

    model_config = ConfigDict(extra="ignore")

    medication_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description=(
            "Medication name exactly as printed. Null only if the document "
            "does not state it."
        ),
    )
    dosage: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Dosage exactly as printed, e.g. '500 mg'. Never compute it.",
    )
    route: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Route exactly as printed, e.g. 'oral', 'IV'.",
    )
    frequency: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Frequency exactly as printed, e.g. 'twice daily'.",
    )
    timing: Optional[str] = Field(
        default=None, max_length=255, description="Timing exactly as printed."
    )
    duration: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Duration exactly as printed, e.g. '5 days'.",
    )
    instructions: Optional[str] = Field(
        default=None,
        description=(
            "Instructions copied verbatim. Do not rewrite, expand, or improve."
        ),
    )
    source_page: Optional[int] = Field(
        default=None,
        ge=1,
        description="1-based page number this item was found on.",
    )
    source_text: Optional[str] = Field(
        default=None,
        description=(
            "Verbatim snippet from the document supporting this item. "
            "Copy it; never paraphrase or fabricate it."
        ),
    )
    needs_review: bool = Field(
        default=False,
        description=(
            "True when the item is ambiguous, incomplete, or uncertain. "
            "A human must confirm it before it is relied upon."
        ),
    )


class ExtractedAppointment(BaseModel):
    """A follow-up appointment exactly as written on the document."""

    model_config = ConfigDict(extra="ignore")

    appointment_type: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Type exactly as printed, e.g. 'Cardiology follow-up'.",
    )
    doctor_or_department: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Doctor name or department exactly as printed.",
    )
    appointment_date: Optional[str] = Field(
        default=None,
        description=(
            "Date exactly as printed. Do not reformat, resolve, or infer it."
        ),
    )
    appointment_time: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Time exactly as printed.",
    )
    location: Optional[str] = Field(
        default=None, max_length=500, description="Location exactly as printed."
    )
    instructions: Optional[str] = Field(
        default=None,
        description="Instructions copied verbatim. Never add your own.",
    )
    source_page: Optional[int] = Field(
        default=None, ge=1, description="1-based page number."
    )
    source_text: Optional[str] = Field(
        default=None,
        description="Verbatim supporting snippet. Never fabricate it.",
    )
    needs_review: bool = Field(
        default=False,
        description="True when ambiguous or incomplete. Requires human review.",
    )


class ExtractedWarningSymptom(BaseModel):
    """A warning sign or 'seek help if' instruction from the document."""

    model_config = ConfigDict(extra="ignore")

    symptom: Optional[str] = Field(
        default=None,
        description="Warning symptom or instruction exactly as printed.",
    )
    instruction: Optional[str] = Field(
        default=None,
        description="Associated instruction copied verbatim from the document.",
    )
    source_page: Optional[int] = Field(
        default=None, ge=1, description="1-based page number."
    )
    source_text: Optional[str] = Field(
        default=None,
        description="Verbatim supporting snippet. Never fabricate it.",
    )
    needs_review: bool = Field(
        default=False,
        description=(
            "Always True for this category. Stored verbatim as a "
            "patient-reported record; it is not a triage or risk assessment."
        ),
    )

    @field_validator("needs_review", mode="after")
    @classmethod
    def _always_needs_review(cls, value: bool) -> bool:
        """
        Warning symptoms are never treated as machine-assessed.

        Forcing this True guarantees the stored record is always visibly
        pending human confirmation, matching the Phase 1 safety boundary
        where warning_symptom rows are patient-reported data only.
        """
        return True


class StructuredDischargeExtraction(BaseModel):
    """Top-level structured extraction contract returned by the LLM."""

    model_config = ConfigDict(extra="ignore")

    patient_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Patient name exactly as printed, if present.",
    )
    hospital_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Hospital/facility name exactly as printed, if present.",
    )
    admission_date: Optional[str] = Field(
        default=None, description="Admission date exactly as printed, if present."
    )
    discharge_date: Optional[str] = Field(
        default=None,
        description="Discharge date exactly as printed, if present."
    )

    medications: List[ExtractedMedication] = Field(
        default_factory=list,
        description="Medications listed on the document. Empty list if none.",
    )
    appointments: List[ExtractedAppointment] = Field(
        default_factory=list,
        description="Follow-up appointments listed. Empty list if none.",
    )
    warning_symptoms: List[ExtractedWarningSymptom] = Field(
        default_factory=list,
        description="Warning signs/instructions listed. Empty list if none.",
    )


# ── Result envelope returned to API clients ───────────────────────────────────

class ExtractionCounts(BaseModel):
    """How many items of each kind were produced."""

    medications: int = 0
    appointments: int = 0
    warning_symptoms: int = 0
    needs_review: int = 0


class ExtractionResultResponse(BaseModel):
    """Structured result of an extraction run, safe to return over the API.

    Contains counts and identifiers only — never the full document text.
    """

    document_id: str
    patient_id: str
    provider: str
    model: str
    processing_status: str
    ocr_status: str
    extraction_status: str
    page_count: Optional[int] = None
    counts: ExtractionCounts = Field(default_factory=ExtractionCounts)
    created_appointment_ids: List[str] = Field(default_factory=list)
    created_medication_ids: List[str] = Field(default_factory=list)
    created_warning_symptom_ids: List[str] = Field(default_factory=list)
    message: Optional[str] = None
