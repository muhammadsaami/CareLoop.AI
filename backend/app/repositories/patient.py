"""
CareLoop AI — Patient Repository
Handles all direct database access for Patient records.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.patient import Patient
from app.schemas.patient import PatientCreate, PatientUpdate


class PatientRepository:
    """Database access layer for Patient."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, data: PatientCreate) -> Patient:
        patient = Patient(**data.model_dump())
        self._db.add(patient)
        self._db.flush()  # flush to get generated id; caller commits
        self._db.refresh(patient)
        return patient

    def get_by_id(self, patient_id: uuid.UUID) -> Optional[Patient]:
        stmt = select(Patient).where(Patient.id == patient_id)
        return self._db.scalars(stmt).first()

    def list_all(self, skip: int = 0, limit: int = 100) -> List[Patient]:
        stmt = select(Patient).order_by(Patient.created_at.desc()).offset(skip).limit(limit)
        return list(self._db.scalars(stmt).all())

    def update(self, patient: Patient, data: PatientUpdate) -> Patient:
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(patient, field, value)
        self._db.flush()
        self._db.refresh(patient)
        return patient

    def delete(self, patient: Patient) -> None:
        self._db.delete(patient)
        self._db.flush()

    def has_related_records(self, patient_id: uuid.UUID) -> bool:
        """Check if the patient has any dependent healthcare records."""
        from app.models.medication import Medication
        from app.models.appointment import Appointment
        from app.models.warning_symptom import WarningSymptom
        from app.models.checkin import CheckIn
        from app.models.adherence_log import AdherenceLog

        for model in (Medication, Appointment, WarningSymptom, CheckIn, AdherenceLog):
            count = self._db.query(model).filter(
                model.patient_id == patient_id
            ).count()
            if count > 0:
                return True
        return False
