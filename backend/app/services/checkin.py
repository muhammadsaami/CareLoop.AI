"""
CareLoop AI — CheckIn Service
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.checkin import CheckIn
from app.repositories.checkin import CheckInRepository
from app.schemas.checkin import CheckInCreate
from app.services.patient import PatientService

logger = get_logger(__name__)


class CheckInService:
    def __init__(self, db: Session) -> None:
        self._repo = CheckInRepository(db)
        self._patient_svc = PatientService(db)
        self._db = db

    def create_checkin(self, patient_id: uuid.UUID, data: CheckInCreate) -> CheckIn:
        self._patient_svc.get_patient(patient_id)
        try:
            checkin = self._repo.create(patient_id, data)
            self._db.commit()
            logger.info("CheckIn created: id=%s for patient=%s flagged=%s",
                        checkin.id, patient_id, checkin.flagged)
            return checkin
        except Exception:
            self._db.rollback()
            logger.exception("Failed to create check-in for patient=%s", patient_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create check-in",
            )

    def get_checkin(self, checkin_id: uuid.UUID) -> CheckIn:
        checkin = self._repo.get_by_id(checkin_id)
        if checkin is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Check-in not found",
            )
        return checkin

    def list_checkins(self, patient_id: uuid.UUID) -> List[CheckIn]:
        self._patient_svc.get_patient(patient_id)
        return self._repo.list_for_patient(patient_id)
