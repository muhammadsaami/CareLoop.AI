"""
CareLoop AI — Escalation Repository (Phase 6)

Persistence for the audit trail.  The two queries that matter beyond ordinary
CRUD are both idempotency guards:

  * `get_by_checkin_and_rule`  - is this escalation already recorded?
  * `find_pending_untried`     - which escalations still need a caregiver
                                  notification attempt?

Both exist so that re-running the submission path, the Celery task, or an
operator's manual retry cannot produce a second escalation or a second
notification.  The unique index on (checkin_id, rule_code) is the authoritative
guard; these turn the common case into a clean, readable outcome instead of an
IntegrityError.
"""
from __future__ import annotations

import uuid
import datetime as dt
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.escalation import Escalation, EscalationStatus


class EscalationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, escalation: Escalation) -> Escalation:
        self._db.add(escalation)
        self._db.flush()
        self._db.refresh(escalation)
        return escalation

    def get_by_id(self, escalation_id: uuid.UUID) -> Optional[Escalation]:
        """
        An escalation by id, with its notification pre-loaded.

        `selectinload` because every read of an escalation displays the
        delivery state, and a lazy load per row turns a 100-row history into
        101 queries.
        """
        stmt = (
            select(Escalation)
            .options(selectinload(Escalation.notification))
            .where(Escalation.id == escalation_id)
        )
        return self._db.scalars(stmt).first()

    def get_by_checkin_and_rule(
        self, checkin_id: uuid.UUID, rule_code: str
    ) -> Optional[Escalation]:
        """
        The escalation for this (check-in, rule) pair, if it already exists.

        The idempotency check the submission path runs before inserting.
        """
        stmt = select(Escalation).where(
            Escalation.checkin_id == checkin_id,
            Escalation.rule_code == rule_code,
        )
        return self._db.scalars(stmt).first()

    def list_for_checkin(
        self, checkin_id: uuid.UUID
    ) -> List[Escalation]:
        """Every escalation raised by one check-in, in deterministic order."""
        stmt = (
            select(Escalation)
            .options(selectinload(Escalation.notification))
            .where(Escalation.checkin_id == checkin_id)
            .order_by(Escalation.rule_code.asc(), Escalation.created_at.asc())
        )
        return list(self._db.scalars(stmt).all())

    def list_for_patient(
        self,
        patient_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
        status: Optional[EscalationStatus] = None,
    ) -> List[Escalation]:
        """A patient's escalations, newest first, optionally by status."""
        stmt = (
            select(Escalation)
            .options(selectinload(Escalation.notification))
            .where(Escalation.patient_id == patient_id)
        )
        if status is not None:
            stmt = stmt.where(Escalation.status == status)
        stmt = (
            stmt.order_by(Escalation.created_at.desc(), Escalation.id.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def count_for_patient(
        self, patient_id: uuid.UUID, status: Optional[EscalationStatus] = None
    ) -> int:
        """Count for a paged envelope, without loading the rows."""
        stmt = select(func.count()).select_from(Escalation).where(
            Escalation.patient_id == patient_id
        )
        if status is not None:
            stmt = stmt.where(Escalation.status == status)
        return int(self._db.scalar(stmt) or 0)

    def find_pending_untried(
        self,
        *,
        now: dt.datetime,
        limit: int = 100,
    ) -> List[Escalation]:
        """
        Escalations that still need a notification attempt.

        Two conditions, and both are load-bearing:

          * `status == pending`  - not yet notified.
          * `notification_id IS NULL` - no Phase 5 notification record has been
            materialised.  This is what makes the task re-runnable: a crash
            between "escalation committed" and "notification committed" leaves
            a row the next run picks up, while a row that already has a
            notification is left alone so a retry cannot double-send.

        `notification_blocked_reason IS NULL` is also required.  An escalation
        with no caregiver contact on file was deliberately parked, and
        re-attempting it every five minutes would fill the logs with a
        condition an operator has to fix in the patient record, not retry.
        """
        stmt = (
            select(Escalation)
            .options(selectinload(Escalation.notification))
            .where(
                Escalation.status == EscalationStatus.pending,
                Escalation.notification_id.is_(None),
                Escalation.notification_blocked_reason.is_(None),
                Escalation.created_at <= now,
            )
            .order_by(Escalation.created_at.asc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def update(self, escalation: Escalation, **fields: object) -> Escalation:
        for key, value in fields.items():
            setattr(escalation, key, value)
        self._db.flush()
        self._db.refresh(escalation)
        return escalation
