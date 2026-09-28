"""
CareLoop AI — Patient Service
Contains application-level operations for patients.
"""
from __future__ import annotations

import uuid
from typing import Collection, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.patient import Patient
from app.repositories.patient import PatientRepository
from app.schemas.patient import PatientCreate, PatientUpdate

logger = get_logger(__name__)


class PatientService:
    def __init__(self, db: Session) -> None:
        self._repo = PatientRepository(db)
        self._db = db

    def create_patient(self, data: PatientCreate) -> Patient:
        try:
            patient = self._repo.create(data)
            self._db.commit()
            logger.info("Patient created: id=%s", patient.id)
            return patient
        except Exception:
            self._db.rollback()
            logger.exception("Failed to create patient")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create patient",
            )

    def get_patient(self, patient_id: uuid.UUID) -> Patient:
        patient = self._repo.get_by_id(patient_id)
        if patient is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient not found",
            )
        return patient

    def list_patients(
        self,
        skip: int = 0,
        limit: int = 100,
        patient_ids: Optional[Collection[uuid.UUID]] = None,
    ) -> List[Patient]:
        """
        List patients, optionally restricted to the caller's authorized set.

        `patient_ids=None` means unrestricted and exists for internal callers
        (Celery tasks, tests). The HTTP route always passes an explicit set -
        see `app.api.routes.patients.list_patients` - so there is no code path
        where an authenticated user reaches this method unscoped.
        """
        return self._repo.list_all(
            skip=skip, limit=limit, patient_ids=patient_ids
        )

    def update_patient(self, patient_id: uuid.UUID, data: PatientUpdate) -> Patient:
        patient = self.get_patient(patient_id)
        try:
            updated = self._repo.update(patient, data)
            self._db.commit()
            logger.info("Patient updated: id=%s", patient_id)
            return updated
        except Exception:
            self._db.rollback()
            logger.exception("Failed to update patient id=%s", patient_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update patient",
            )

    def delete_patient(self, patient_id: uuid.UUID) -> None:
        patient = self.get_patient(patient_id)

        # Prevent deletion if the patient has dependent healthcare records
        if self._repo.has_related_records(patient_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Cannot delete patient with existing records. "
                    "Remove medications, appointments, check-ins, and other records first."
                ),
            )

        try:
            self._repo.delete(patient)
            self._db.commit()
            logger.info("Patient deleted: id=%s", patient_id)
        except Exception:
            self._db.rollback()
            logger.exception("Failed to delete patient id=%s", patient_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete patient",
            )
