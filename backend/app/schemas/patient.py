"""
CareLoop AI — Patient Pydantic Schemas
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.exceptions import InvalidTimezoneError
from app.core.timezones import validate_timezone_name


class PatientCreate(BaseModel):
    """Schema for creating a new patient."""

    name: str = Field(..., min_length=1, max_length=255, description="Full patient name")
    contact_number: str = Field(
        ..., min_length=1, max_length=50, description="Primary contact number"
    )
    caregiver_contact: Optional[str] = Field(
        None, max_length=50, description="Caregiver's contact number (optional)"
    )
    discharge_date: Optional[date] = Field(
        None, description="Hospital discharge date (optional)"
    )
    timezone: str = Field(
        "UTC",
        max_length=64,
        description=(
            "IANA timezone name, e.g. 'Asia/Kolkata'.  Phase 5 schedules every "
            "reminder against this, so a wrong value shifts a dose by hours."
        ),
    )

    @field_validator("contact_number", "caregiver_contact", mode="before")
    @classmethod
    def strip_whitespace(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            return value.strip()
        return value

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        # Validated here rather than at reminder-creation time so the error
        # surfaces where the mistake was made, and so a bad zone can never be
        # stored and then silently mis-schedule every reminder.
        #
        # `InvalidTimezoneError` derives from CareLoopError, not ValueError, so
        # raising it directly would escape the validator and surface as a 500.
        # Re-raising as ValueError is what turns a client typo into a 422.
        try:
            return validate_timezone_name(value)
        except InvalidTimezoneError as exc:
            raise ValueError(str(exc)) from exc


class PatientUpdate(BaseModel):
    """Schema for partially updating a patient.  All fields optional."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    contact_number: Optional[str] = Field(None, min_length=1, max_length=50)
    caregiver_contact: Optional[str] = Field(None, max_length=50)
    discharge_date: Optional[date] = None
    timezone: Optional[str] = Field(
        None, max_length=64, description="IANA timezone name."
    )

    @field_validator("contact_number", "caregiver_contact", mode="before")
    @classmethod
    def strip_whitespace(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            return value.strip()
        return value

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return validate_timezone_name(value)
        except InvalidTimezoneError as exc:
            raise ValueError(str(exc)) from exc


class PatientResponse(BaseModel):
    """Schema for patient API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    contact_number: str
    caregiver_contact: Optional[str]
    discharge_date: Optional[date]
    timezone: str = "UTC"
    created_at: datetime
    updated_at: datetime
