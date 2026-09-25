"""
CareLoop AI — DischargeDocument Repository (Phase 2)
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.discharge_document import DischargeDocument
from app.models.extraction_run import ExtractionRun


class DischargeDocumentRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, **fields) -> DischargeDocument:
        document = DischargeDocument(**fields)
        self._db.add(document)
        self._db.flush()
        self._db.refresh(document)
        return document

    def get_by_id(self, document_id: uuid.UUID) -> Optional[DischargeDocument]:
        stmt = (
            select(DischargeDocument)
            .where(DischargeDocument.id == document_id)
            .options(selectinload(DischargeDocument.extraction_runs))
        )
        return self._db.scalars(stmt).first()

    def find_by_hash(
        self, patient_id: uuid.UUID, sha256_hash: str
    ) -> Optional[DischargeDocument]:
        """Duplicate lookup: same patient, identical file bytes."""
        stmt = select(DischargeDocument).where(
            DischargeDocument.patient_id == patient_id,
            DischargeDocument.sha256_hash == sha256_hash,
        )
        return self._db.scalars(stmt).first()

    def list_for_patient(
        self, patient_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> List[DischargeDocument]:
        stmt = (
            select(DischargeDocument)
            .where(DischargeDocument.patient_id == patient_id)
            .order_by(DischargeDocument.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def update(self, document: DischargeDocument, **fields) -> DischargeDocument:
        for key, value in fields.items():
            setattr(document, key, value)
        self._db.flush()
        self._db.refresh(document)
        return document

    # ── Extraction runs ─────────────────────────────────────────────────────

    def create_extraction_run(self, **fields) -> ExtractionRun:
        run = ExtractionRun(**fields)
        self._db.add(run)
        self._db.flush()
        self._db.refresh(run)
        return run

    def list_extraction_runs(
        self, document_id: uuid.UUID
    ) -> List[ExtractionRun]:
        stmt = (
            select(ExtractionRun)
            .where(ExtractionRun.document_id == document_id)
            .order_by(ExtractionRun.started_at.desc())
        )
        return list(self._db.scalars(stmt).all())

    def latest_extraction_run(
        self, document_id: uuid.UUID
    ) -> Optional[ExtractionRun]:
        runs = self.list_extraction_runs(document_id)
        return runs[0] if runs else None
