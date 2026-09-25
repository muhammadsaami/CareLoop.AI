"""
CareLoop AI — Patient Pydantic Schemas
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("contact_number", "caregiver_contact", mode="before")
    @classmethod
    def strip_whitespace(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            return value.strip()
        return value


class PatientUpdate(BaseModel):
    """Schema for partially updating a patient.  All fields optional."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    contact_number: Optional[str] = Field(None, min_length=1, max_length=50)
    caregiver_contact: Optional[str] = Field(None, max_length=50)
    discharge_date: Optional[date] = None

    @field_validator("contact_number", "caregiver_contact", mode="before")
    @classmethod
    def strip_whitespace(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            return value.strip()
        return value


class PatientResponse(BaseModel):
    """Schema for patient API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    contact_number: str
    caregiver_contact: Optional[str]
    discharge_date: Optional[date]
    created_at: datetime
    updated_at: datetime
