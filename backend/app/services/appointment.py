"""
CareLoop AI — Appointment Service
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.appointment import Appointment
from app.repositories.appointment import AppointmentRepository
from app.schemas.appointment import AppointmentCreate, AppointmentUpdate
from app.services.patient import PatientService

logger = get_logger(__name__)


class AppointmentService:
    def __init__(self, db: Session) -> None:
        self._repo = AppointmentRepository(db)
        self._patient_svc = PatientService(db)
        self._db = db

    def create_appointment(self, patient_id: uuid.UUID, data: AppointmentCreate) -> Appointment:
        self._patient_svc.get_patient(patient_id)
        try:
            appointment = self._repo.create(patient_id, data)
            self._db.commit()
            logger.info("Appointment created: id=%s for patient=%s", appointment.id, patient_id)
            return appointment
        except Exception:
            self._db.rollback()
            logger.exception("Failed to create appointment for patient=%s", patient_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create appointment",
            )

    def get_appointment(self, appointment_id: uuid.UUID) -> Appointment:
        appointment = self._repo.get_by_id(appointment_id)
        if appointment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found",
            )
        return appointment

    def list_appointments(self, patient_id: uuid.UUID) -> List[Appointment]:
        self._patient_svc.get_patient(patient_id)
        return self._repo.list_for_patient(patient_id)

    def update_appointment(self, appointment_id: uuid.UUID, data: AppointmentUpdate) -> Appointment:
        appointment = self.get_appointment(appointment_id)
        try:
            updated = self._repo.update(appointment, data)
            self._db.commit()
            return updated
        except Exception:
            self._db.rollback()
            logger.exception("Failed to update appointment id=%s", appointment_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update appointment",
            )

    def delete_appointment(self, appointment_id: uuid.UUID) -> None:
        appointment = self.get_appointment(appointment_id)
        try:
            self._repo.delete(appointment)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete appointment",
            )
