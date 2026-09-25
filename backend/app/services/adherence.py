"""
CareLoop AI — AdherenceLog Service
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.adherence_log import AdherenceLog
from app.repositories.adherence import AdherenceLogRepository
from app.repositories.medication import MedicationRepository
from app.schemas.adherence_log import AdherenceLogCreate, AdherenceLogUpdate
from app.services.patient import PatientService

logger = get_logger(__name__)


class AdherenceService:
    def __init__(self, db: Session) -> None:
        self._repo = AdherenceLogRepository(db)
        self._med_repo = MedicationRepository(db)
        self._patient_svc = PatientService(db)
        self._db = db

    def create_log(self, patient_id: uuid.UUID, data: AdherenceLogCreate) -> AdherenceLog:
        # Verify patient exists
        self._patient_svc.get_patient(patient_id)

        # Verify medication exists and belongs to this patient
        medication = self._med_repo.get_by_id(data.medication_id)
        if medication is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Medication not found",
            )
        if medication.patient_id != patient_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Medication does not belong to this patient",
            )

        try:
            log = self._repo.create(patient_id, data)
            self._db.commit()
            return log
        except Exception:
            self._db.rollback()
            logger.exception("Failed to create adherence log for patient=%s", patient_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create adherence log",
            )

    def get_log(self, log_id: uuid.UUID) -> AdherenceLog:
        log = self._repo.get_by_id(log_id)
        if log is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Adherence log not found",
            )
        return log

    def list_logs(self, patient_id: uuid.UUID) -> List[AdherenceLog]:
        self._patient_svc.get_patient(patient_id)
        return self._repo.list_for_patient(patient_id)

    def update_log(self, log_id: uuid.UUID, data: AdherenceLogUpdate) -> AdherenceLog:
        log = self.get_log(log_id)
        try:
            updated = self._repo.update(log, data)
            self._db.commit()
            return updated
        except Exception:
            self._db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update adherence log",
            )
