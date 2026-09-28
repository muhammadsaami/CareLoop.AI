"""
CareLoop AI — CheckIn Pydantic Schemas

Phase 1 defined `CheckInCreate` / `CheckInResponse` around one free-text
`response_text` and a client-supplied `flagged` flag.  Both are kept
unchanged and keep working.

Phase 6 adds a SEPARATE, STRUCTURED pair.  The split is the point:

  * `CheckInCreate`  - the Phase 1 free-text diary entry.  Unchanged.
  * `DailyCheckInCreate` - the Phase 6 evaluated check-in.  Coded answers
    only, no free text at all, `extra="forbid"`.

WHY PHASE 6 REFUSES FREE TEXT
There is no way to evaluate "feeling a bit rough, maybe a headache?" without
inferring meaning from it, and inferring clinical meaning from prose is exactly
what the safety boundary forbids.  So the Phase 6 endpoint has no text field.
A patient reports each of THEIR OWN documented warning symptoms by selecting
its id, and answers everything else from a fixed set of options.  The result is
either matchable against the configured rules or explicitly unevaluable - there
is no third outcome where the system has quietly guessed.

`extra="forbid"` matters here more than the Phase 5 schemas' usual case: a
caller sending `responses_text` or `severity` would otherwise have it silently
dropped and receive a 201, believing a clinical answer had been recorded.
"""
from __future__ import annotations

import uuid
import datetime as dt
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.timezones import DEFAULT_TIMEZONE, validate_timezone_name
from app.models.checkin import (
    CheckInStatus,
    ConditionChange,
    SymptomChange,
    WellbeingAnswer,
)
from app.models.escalation import (
    EscalationCategory,
    EscalationStatus,
    EscalationWorkflow,
)
from app.models.warning_symptom import SymptomSeverity

# ── Phase 1 (unchanged) ─────────────────────────────────────────────────


class CheckInCreate(BaseModel):
    """Schema for creating a check-in record."""

    date: dt.date = Field(..., description="Date of the check-in")
    response_text: str = Field(..., min_length=1)
    flagged: bool = False
    flag_reason: Optional[str] = None


class CheckInResponse(BaseModel):
    """Schema for check-in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    date: dt.date
    response_text: str
    flagged: bool
    flag_reason: Optional[str]
    created_at: dt.datetime


# ── Phase 6: structured, evaluated daily check-ins ───────────────────────


class SymptomReportCreate(BaseModel):
    """
    "Is this documented warning symptom happening right now, and has it
    changed?"

    `symptom_id` must reference a `WarningSymptom` owned by THIS patient; the
    service enforces that and records `unrecognised_warning_symptom` for any id
    that fails, rather than raising.  Reporting a symptom you do not have on
    record is a meaningful answer, not a malformed request.
    """

    model_config = ConfigDict(extra="forbid")

    symptom_id: uuid.UUID
    change: SymptomChange


class DailyCheckInCreate(BaseModel):
    """
    One day's structured answers.

    Deliberately has no free-text field.  `flagged` and `flag_reason` are
    absent by design: a client must not be able to assert its own clinical
    flag, which is what Phase 1 allowed.  `status` is computed by the
    deterministic rule layer and is not accepted from the request.
    """

    model_config = ConfigDict(extra="forbid")

    #: Defaults to today IN THE PATIENT'S TIMEZONE, resolved server-side from
    #: the patient record - never from the caller's clock, or a patient in
    #: Asia/Kolkata answering at 00:30 would file today's check-in under
    #: yesterday.
    date: Optional[dt.date] = None
    timezone: Optional[str] = Field(
        None,
        max_length=64,
        description=(
            "IANA zone the answers were given in. Defaults to the patient's "
            "recorded timezone. Must match it; a check-in cannot be filed "
            "against a zone the patient does not live in."
        ),
    )

    general_wellbeing: Optional[WellbeingAnswer] = None
    condition_change: Optional[ConditionChange] = None
    warning_symptoms: list[SymptomReportCreate] = Field(
        default_factory=list,
        description=(
            "The patient's own documented warning symptoms, by id. Empty is a "
            "valid answer meaning 'none of them'."
        ),
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_timezone_name(value.strip() or DEFAULT_TIMEZONE)

    @model_validator(mode="after")
    def validate_answer_set(self) -> "DailyCheckInCreate":
        """
        Structural checks that are about the REQUEST, not the patient.

        Whether an answer is *evaluable* is not decided here - that is the rule
        layer's job, and it answers with `needs_review` rather than an error.
        These checks only reject input that has no meaning at all.
        """
        if not (
            self.general_wellbeing
            or self.condition_change
            or self.warning_symptoms
        ):
            raise ValueError(
                "At least one answer is required: general wellbeing, condition "
                "change, or a warning symptom report."
            )
        seen: set[uuid.UUID] = set()
        for report in self.warning_symptoms:
            if report.symptom_id in seen:
                # Two contradictory answers about the same symptom cannot be
                # resolved without guessing which one the patient meant.
                raise ValueError(
                    f"Duplicate entry for warning symptom {report.symptom_id}: "
                    "report each symptom once."
                )
            seen.add(report.symptom_id)
        return self


class WarningSymptomOption(BaseModel):
    """A documented warning symptom, as offered to the patient."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    severity: SymptomSeverity


class CheckInQuestion(BaseModel):
    """
    One configurable question, described rather than hard-coded into a client.

    Exposed by `GET /checkins/questions` so the question set is a contract the
    client can read instead of a set of strings this service assumes.  No
    clinical content lives here: `key` is a code, and the option values are the
    enum members from `app.models.checkin`.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    prompt: str
    #: `single_select` or `multi_select`.
    answer_type: str
    options: list[str]
    #: True when the client must offer the patient's own warning symptoms.
    sourced_from_warning_symptoms: bool = False


class CheckInQuestionSet(BaseModel):
    """The versioned question set plus the patient's applicable options."""

    version: str
    questions: list[CheckInQuestion]
    #: Empty when the patient has no documented warning symptoms, which is a
    #: legitimate state - the symptom question is then simply not answerable
    #: and the rest of the check-in still works.
    available_warning_symptoms: list[WarningSymptomOption]


class EscalationSummary(BaseModel):
    """
    The safe, non-diagnostic projection of an escalation.

    What a patient-facing or caregiver-facing caller is shown.  It carries the
    rule code, the category, and the CONFIGURED workflow - never the patient's
    answers, never the symptom description, never a severity this system
    derived.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checkin_id: uuid.UUID
    rule_code: str
    rule_version: str
    category: EscalationCategory
    workflow: EscalationWorkflow
    status: EscalationStatus
    severity: Optional[str]
    reason_code: str
    created_at: dt.datetime


class DailyCheckInResponse(BaseModel):
    """
    A submitted check-in, its evaluation, and what the patient is told.

    `patient_message` is the ONLY clinical-facing string this system produces,
    and it is assembled from the configured workflow - see
    `app.services.daily_checkin._patient_message` for why it says what it
    says and, more importantly, what it refuses to say.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    date: dt.date
    timezone: str
    status: CheckInStatus
    responses: Optional[dict]
    needs_review: bool
    review_reason: Optional[str]
    completed_at: Optional[dt.datetime]
    created_at: dt.datetime

    escalations: list[EscalationSummary] = Field(default_factory=list)
    #: Non-diagnostic, workflow-only guidance.  Never names a condition.
    patient_message: str


class DailyCheckInHistoryItem(BaseModel):
    """
    One row of history.

    `responses` is included because a patient reviewing their own history has a
    legitimate need to see what they answered, and it holds only the coded
    values they supplied - no text, nothing they did not type as an option.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    date: dt.date
    timezone: Optional[str]
    status: CheckInStatus
    needs_review: bool
    review_reason: Optional[str]
    responses: dict
    escalation_count: int
    completed_at: Optional[dt.datetime]


class EscalationResponse(BaseModel):
    """
    Full escalation detail for an authorized reader.

    Reachable only through a patient-scoped path or a direct id fetch whose
    ownership is verified, exactly as Phase 5 does for reminders.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    checkin_id: uuid.UUID
    rule_code: str
    rule_version: str
    category: EscalationCategory
    workflow: EscalationWorkflow
    status: EscalationStatus
    severity: Optional[str]
    warning_symptom_id: Optional[uuid.UUID]
    reason_code: str
    notification_id: Optional[uuid.UUID]
    notification_blocked_reason: Optional[str]
    notified_at: Optional[dt.datetime]
    acknowledged_at: Optional[dt.datetime]
    resolved_at: Optional[dt.datetime]
    resolution_note: Optional[str]
    created_at: dt.datetime
    updated_at: dt.datetime


class EscalationTransitionRequest(BaseModel):
    """
    A human closing the loop on an escalation.

    `resolution_note` is bounded and free-form because a human genuinely needs
    to write something, but it is stored only on the escalation row - it is
    never rendered into a notification body and never logged.
    """

    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = Field(None, max_length=255)


class CheckInHistoryResponse(BaseModel):
    """Paged history envelope."""

    total: int
    skip: int
    limit: int
    items: list[DailyCheckInHistoryItem]
