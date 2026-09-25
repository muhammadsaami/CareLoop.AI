"""
CareLoop AI — Medication Pydantic Schemas
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MedicationCreate(BaseModel):
    """Schema for creating a medication record."""

    name: str = Field(..., min_length=1, max_length=255)
    dosage: str = Field(..., min_length=1, max_length=100)
    frequency: str = Field(..., min_length=1, max_length=100)
    timing: Optional[str] = Field(None, max_length=255)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    instructions: Optional[str] = None


class MedicationUpdate(BaseModel):
    """Schema for partially updating a medication record."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    dosage: Optional[str] = Field(None, min_length=1, max_length=100)
    frequency: Optional[str] = Field(None, min_length=1, max_length=100)
    timing: Optional[str] = Field(None, max_length=255)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    instructions: Optional[str] = None


class MedicationResponse(BaseModel):
    """Schema for medication API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    name: str
    dosage: str
    frequency: str
    timing: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    instructions: Optional[str]
    created_at: datetime
    updated_at: datetime
