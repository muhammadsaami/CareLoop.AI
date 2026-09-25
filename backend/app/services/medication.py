"""
CareLoop AI — Medication Service
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.medication import Medication
from app.repositories.medication import MedicationRepository
from app.schemas.medication import MedicationCreate, MedicationUpdate
from app.services.patient import PatientService

logger = get_logger(__name__)


class MedicationService:
    def __init__(self, db: Session) -> None:
        self._repo = MedicationRepository(db)
        self._patient_svc = PatientService(db)
        self._db = db

    def create_medication(self, patient_id: uuid.UUID, data: MedicationCreate) -> Medication:
        # Verify patient exists — raises 404 if not
        self._patient_svc.get_patient(patient_id)
        try:
            medication = self._repo.create(patient_id, data)
            self._db.commit()
            logger.info("Medication created: id=%s for patient=%s", medication.id, patient_id)
            return medication
        except Exception:
            self._db.rollback()
            logger.exception("Failed to create medication for patient=%s", patient_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create medication",
            )

    def get_medication(self, medication_id: uuid.UUID) -> Medication:
        medication = self._repo.get_by_id(medication_id)
        if medication is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Medication not found",
            )
        return medication

    def list_medications(self, patient_id: uuid.UUID) -> List[Medication]:
        self._patient_svc.get_patient(patient_id)
        return self._repo.list_for_patient(patient_id)

    def update_medication(self, medication_id: uuid.UUID, data: MedicationUpdate) -> Medication:
        medication = self.get_medication(medication_id)
        try:
            updated = self._repo.update(medication, data)
            self._db.commit()
            return updated
        except Exception:
            self._db.rollback()
            logger.exception("Failed to update medication id=%s", medication_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update medication",
            )

    def delete_medication(self, medication_id: uuid.UUID) -> None:
        medication = self.get_medication(medication_id)
        try:
            self._repo.delete(medication)
            self._db.commit()
            logger.info("Medication deleted: id=%s", medication_id)
        except Exception:
            self._db.rollback()
            logger.exception("Failed to delete medication id=%s", medication_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete medication",
            )
