"""
CareLoop AI — WarningSymptom Pydantic Schemas
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.warning_symptom import SymptomSeverity


class WarningSymptomCreate(BaseModel):
    """Schema for creating a warning symptom record."""

    description: str = Field(..., min_length=1)
    severity: SymptomSeverity


class WarningSymptomUpdate(BaseModel):
    """Schema for partially updating a warning symptom record."""

    description: Optional[str] = Field(None, min_length=1)
    severity: Optional[SymptomSeverity] = None


class WarningSymptomResponse(BaseModel):
    """Schema for warning symptom API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    description: str
    severity: SymptomSeverity
    created_at: datetime
    updated_at: datetime
