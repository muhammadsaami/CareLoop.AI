"""
CareLoop AI — Medication Repository
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.medication import Medication
from app.schemas.medication import MedicationCreate, MedicationUpdate


class MedicationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, patient_id: uuid.UUID, data: MedicationCreate) -> Medication:
        medication = Medication(patient_id=patient_id, **data.model_dump())
        self._db.add(medication)
        self._db.flush()
        self._db.refresh(medication)
        return medication

    def get_by_id(self, medication_id: uuid.UUID) -> Optional[Medication]:
        stmt = select(Medication).where(Medication.id == medication_id)
        return self._db.scalars(stmt).first()

    def list_for_patient(
        self, patient_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> List[Medication]:
        stmt = (
            select(Medication)
            .where(Medication.patient_id == patient_id)
            .order_by(Medication.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def update(self, medication: Medication, data: MedicationUpdate) -> Medication:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(medication, field, value)
        self._db.flush()
        self._db.refresh(medication)
        return medication

    def delete(self, medication: Medication) -> None:
        self._db.delete(medication)
        self._db.flush()
