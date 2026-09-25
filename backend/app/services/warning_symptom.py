"""
CareLoop AI — WarningSymptom Service
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.warning_symptom import WarningSymptom
from app.repositories.warning_symptom import WarningSymptomRepository
from app.schemas.warning_symptom import WarningSymptomCreate, WarningSymptomUpdate
from app.services.patient import PatientService

logger = get_logger(__name__)


class WarningSymptomService:
    def __init__(self, db: Session) -> None:
        self._repo = WarningSymptomRepository(db)
        self._patient_svc = PatientService(db)
        self._db = db

    def create_symptom(self, patient_id: uuid.UUID, data: WarningSymptomCreate) -> WarningSymptom:
        self._patient_svc.get_patient(patient_id)
        try:
            symptom = self._repo.create(patient_id, data)
            self._db.commit()
            return symptom
        except Exception:
            self._db.rollback()
            logger.exception("Failed to create warning symptom for patient=%s", patient_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create warning symptom",
            )

    def get_symptom(self, symptom_id: uuid.UUID) -> WarningSymptom:
        symptom = self._repo.get_by_id(symptom_id)
        if symptom is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Warning symptom not found",
            )
        return symptom

    def list_symptoms(self, patient_id: uuid.UUID) -> List[WarningSymptom]:
        self._patient_svc.get_patient(patient_id)
        return self._repo.list_for_patient(patient_id)

    def update_symptom(self, symptom_id: uuid.UUID, data: WarningSymptomUpdate) -> WarningSymptom:
        symptom = self.get_symptom(symptom_id)
        try:
            updated = self._repo.update(symptom, data)
            self._db.commit()
            return updated
        except Exception:
            self._db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update warning symptom",
            )

    def delete_symptom(self, symptom_id: uuid.UUID) -> None:
        symptom = self.get_symptom(symptom_id)
        try:
            self._repo.delete(symptom)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete warning symptom",
            )
