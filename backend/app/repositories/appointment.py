"""
CareLoop AI — Appointment Repository
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.schemas.appointment import AppointmentCreate, AppointmentUpdate


class AppointmentRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, patient_id: uuid.UUID, data: AppointmentCreate) -> Appointment:
        appointment = Appointment(patient_id=patient_id, **data.model_dump())
        self._db.add(appointment)
        self._db.flush()
        self._db.refresh(appointment)
        return appointment

    def get_by_id(self, appointment_id: uuid.UUID) -> Optional[Appointment]:
        stmt = select(Appointment).where(Appointment.id == appointment_id)
        return self._db.scalars(stmt).first()

    def list_for_patient(
        self, patient_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> List[Appointment]:
        stmt = (
            select(Appointment)
            .where(Appointment.patient_id == patient_id)
            .order_by(Appointment.date.asc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def update(self, appointment: Appointment, data: AppointmentUpdate) -> Appointment:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(appointment, field, value)
        self._db.flush()
        self._db.refresh(appointment)
        return appointment

    def delete(self, appointment: Appointment) -> None:
        self._db.delete(appointment)
        self._db.flush()
