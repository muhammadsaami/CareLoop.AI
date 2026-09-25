"""
CareLoop AI — WarningSymptom Repository
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.warning_symptom import WarningSymptom
from app.schemas.warning_symptom import WarningSymptomCreate, WarningSymptomUpdate


class WarningSymptomRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, patient_id: uuid.UUID, data: WarningSymptomCreate) -> WarningSymptom:
        symptom = WarningSymptom(patient_id=patient_id, **data.model_dump())
        self._db.add(symptom)
        self._db.flush()
        self._db.refresh(symptom)
        return symptom

    def get_by_id(self, symptom_id: uuid.UUID) -> Optional[WarningSymptom]:
        stmt = select(WarningSymptom).where(WarningSymptom.id == symptom_id)
        return self._db.scalars(stmt).first()

    def list_for_patient(
        self, patient_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> List[WarningSymptom]:
        stmt = (
            select(WarningSymptom)
            .where(WarningSymptom.patient_id == patient_id)
            .order_by(WarningSymptom.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def update(self, symptom: WarningSymptom, data: WarningSymptomUpdate) -> WarningSymptom:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(symptom, field, value)
        self._db.flush()
        self._db.refresh(symptom)
        return symptom

    def delete(self, symptom: WarningSymptom) -> None:
        self._db.delete(symptom)
        self._db.flush()
