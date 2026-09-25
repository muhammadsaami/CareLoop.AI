"""
CareLoop AI — Appointment Pydantic Schemas
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.appointment import AppointmentStatus


class AppointmentCreate(BaseModel):
    """Schema for creating an appointment."""

    doctor_name: str = Field(..., min_length=1, max_length=255)
    date: datetime = Field(..., description="Appointment date/time (timezone-aware recommended)")
    location: Optional[str] = Field(None, max_length=500)
    status: AppointmentStatus = AppointmentStatus.scheduled
    notes: Optional[str] = None


class AppointmentUpdate(BaseModel):
    """Schema for partially updating an appointment."""

    doctor_name: Optional[str] = Field(None, min_length=1, max_length=255)
    date: Optional[datetime] = None
    location: Optional[str] = Field(None, max_length=500)
    status: Optional[AppointmentStatus] = None
    notes: Optional[str] = None


class AppointmentResponse(BaseModel):
    """Schema for appointment API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_name: str
    date: datetime
    location: Optional[str]
    status: AppointmentStatus
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
