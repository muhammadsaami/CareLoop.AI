"""
CareLoop AI — Reminder Pydantic Schemas (Phase 5)

Request validation is where "do not invent a schedule" is enforced at the edge.

Note what a medication reminder request does NOT accept: a `frequency` string,
or a free-text schedule.  It accepts a *list of explicit local times* that a
clinician or integrator has already determined.  There is deliberately no field
into which a caller could post a schedule for the server to interpret.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.timezones import DEFAULT_TIMEZONE, validate_timezone_name
from app.models.reminder import ReminderStatus, ReminderType, Recurrence


def _validate_times(value: list[time]) -> list[time]:
    """Reject a duplicate dose slot, which would double-message the patient."""
    if not value:
        raise ValueError("At least one dose time is required.")
    if len(value) > 8:
        raise ValueError("A reminder may not define more than 8 daily times.")
    seen: set[time] = set()
    for item in value:
        if item in seen:
            raise ValueError(
                f"Duplicate dose time {item.strftime('%H:%M')}: a medication "
                "reminder must not repeat the same time twice."
            )
        seen.add(item)
    # Deduplicate seconds: a reminder is a wall-clock time, not a timestamp.
    normalised = [t.replace(second=0, microsecond=0) for t in value]
    normalised.sort()
    return normalised


class MedicationReminderCreate(BaseModel):
    """
    Create a recurring medication reminder from an existing medication.

    `times` is REQUIRED and must come from a clinician or an upstream system
    that already knows the dosing schedule.  This service will not infer times
    from `Medication.frequency`, because that field is free text
    ("twice daily with meals") and guessing from it would put a patient on the
    wrong dose schedule.

    `extra="forbid"` so a `schedule` or `frequency` field is rejected rather
    than ignored: a caller who supplies prose and gets a 201 back would believe
    the schedule was honoured.
    """

    model_config = ConfigDict(extra="forbid")

    medication_id: uuid.UUID = Field(
        ..., description="Existing medication to remind about"
    )
    times: list[time] = Field(
        ...,
        min_length=1,
        max_length=8,
        description=(
            "Explicit local dose times, e.g. ['08:00', '20:00']. Interpreted "
            "in `timezone`. Must be supplied by a clinician; never inferred "
            "from the medication's free-text frequency."
        ),
    )
    timezone: Optional[str] = Field(
        None,
        max_length=64,
        description=(
            "IANA timezone the dose times are given in. Defaults to the "
            "patient's own timezone."
        ),
    )
    recurrence: Recurrence = Recurrence.daily
    recurrence_interval: int = Field(1, ge=1, le=52)
    start_date: Optional[datetime] = Field(
        None,
        description="Optional first eligible instant (UTC). Earlier occurrences are skipped.",
    )
    end_date: Optional[datetime] = Field(
        None, description="Optional last eligible instant (UTC)."
    )
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("times")
    @classmethod
    def _check_times(cls, value: list[time]) -> list[time]:
        return _validate_times(value)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_timezone_name(value)

    @model_validator(mode="after")
    def _check_window(self) -> "MedicationReminderCreate":
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date >= self.end_date
        ):
            raise ValueError("start_date must be earlier than end_date.")
        return self

    @field_validator("start_date", "end_date")
    @classmethod
    def _require_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError(
                "start_date and end_date must be timezone-aware. A naive "
                "datetime cannot be scheduled reliably."
            )
        return value


class AppointmentReminderCreate(BaseModel):
    """
    Create a one-shot reminder ahead of an existing appointment.

    The request supplies ONLY the lead time.  It does not accept the
    appointment's date: that already exists on the `Appointment` row, and
    accepting a second copy would let the two disagree.  The reminder instant
    is derived as `Appointment.date - lead_time_minutes`.

    `extra="forbid"` so a `date` field in the request is rejected outright
    rather than quietly dropped - a caller overriding the appointment time
    deserves an error, not a silent success.
    """

    model_config = ConfigDict(extra="forbid")

    appointment_id: uuid.UUID
    lead_time_minutes: int = Field(
        ...,
        ge=1,
        le=60 * 24 * 7,
        description="How many minutes before the appointment to remind.",
    )
    timezone: Optional[str] = Field(
        None,
        max_length=64,
        description="IANA timezone for display. Defaults to the patient's timezone.",
    )
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_timezone_name(value)


class ReminderUpdate(BaseModel):
    """
    Partially update a reminder.

    Only lifecycle and window fields are mutable.  `local_time`,
    `medication_id`, and `appointment_id` are NOT updatable: changing the dose
    time of an existing rule in place would silently re-point reminders a
    patient has already been sent.  Cancelling and creating a new reminder is
    the explicit path.

    `extra="forbid"` is load-bearing, not tidiness.  Pydantic's default is to
    ignore unknown fields, so a PATCH carrying `local_time` would return 200
    with the schedule unchanged - telling a clinician their edit succeeded while
    the patient keeps getting the old time.  Rejecting it loudly is the only
    safe response for a dose schedule.
    """

    model_config = ConfigDict(extra="forbid")

    status: Optional[ReminderStatus] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("start_date", "end_date")
    @classmethod
    def _require_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError("start_date and end_date must be timezone-aware.")
        return value


class ReminderResponse(BaseModel):
    """Schema for reminder API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    reminder_type: ReminderType
    medication_id: Optional[uuid.UUID]
    appointment_id: Optional[uuid.UUID]
    local_time: Optional[time]
    recurrence: Recurrence
    recurrence_interval: int
    frequency_text: Optional[str]
    appointment_at: Optional[datetime]
    lead_time_minutes: Optional[int]
    timezone: str
    next_occurrence_at: Optional[datetime]
    active_from: Optional[datetime]
    active_until: Optional[datetime]
    status: ReminderStatus
    needs_review: bool
    review_reason: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
