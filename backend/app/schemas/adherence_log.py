"""
CareLoop AI — AdherenceLog Pydantic Schemas
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AdherenceLogCreate(BaseModel):
    """Schema for creating an adherence log entry."""

    medication_id: uuid.UUID
    scheduled_time: datetime = Field(..., description="Scheduled dose time (timezone-aware recommended)")
    taken: bool = False
    taken_time: Optional[datetime] = None


class AdherenceLogUpdate(BaseModel):
    """Schema for updating an adherence log (mark as taken, etc.)."""

    taken: Optional[bool] = None
    taken_time: Optional[datetime] = None


class AdherenceLogResponse(BaseModel):
    """Schema for adherence log API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    medication_id: uuid.UUID
    scheduled_time: datetime
    taken: bool
    taken_time: Optional[datetime]
    created_at: datetime
