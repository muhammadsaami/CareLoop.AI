"""
CareLoop AI — AdherenceLog Repository
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.adherence_log import AdherenceLog
from app.schemas.adherence_log import AdherenceLogCreate, AdherenceLogUpdate


class AdherenceLogRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, patient_id: uuid.UUID, data: AdherenceLogCreate) -> AdherenceLog:
        log = AdherenceLog(patient_id=patient_id, **data.model_dump())
        self._db.add(log)
        self._db.flush()
        self._db.refresh(log)
        return log

    def get_by_id(self, log_id: uuid.UUID) -> Optional[AdherenceLog]:
        stmt = select(AdherenceLog).where(AdherenceLog.id == log_id)
        return self._db.scalars(stmt).first()

    def list_for_patient(
        self, patient_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> List[AdherenceLog]:
        stmt = (
            select(AdherenceLog)
            .where(AdherenceLog.patient_id == patient_id)
            .order_by(AdherenceLog.scheduled_time.asc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def update(self, log: AdherenceLog, data: AdherenceLogUpdate) -> AdherenceLog:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(log, field, value)
        self._db.flush()
        self._db.refresh(log)
        return log
