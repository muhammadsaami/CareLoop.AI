"""
CareLoop AI — CheckIn Repository
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.checkin import CheckIn
from app.schemas.checkin import CheckInCreate


class CheckInRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, patient_id: uuid.UUID, data: CheckInCreate) -> CheckIn:
        checkin = CheckIn(patient_id=patient_id, **data.model_dump())
        self._db.add(checkin)
        self._db.flush()
        self._db.refresh(checkin)
        return checkin

    def get_by_id(self, checkin_id: uuid.UUID) -> Optional[CheckIn]:
        stmt = select(CheckIn).where(CheckIn.id == checkin_id)
        return self._db.scalars(stmt).first()

    def list_for_patient(
        self, patient_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> List[CheckIn]:
        stmt = (
            select(CheckIn)
            .where(CheckIn.patient_id == patient_id)
            .order_by(CheckIn.date.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())
