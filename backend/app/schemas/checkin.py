"""
CareLoop AI — CheckIn Pydantic Schemas
"""
from __future__ import annotations

import uuid
import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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
