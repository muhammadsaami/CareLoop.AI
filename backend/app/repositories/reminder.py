"""
CareLoop AI — Reminder Repository (Phase 5)

Persistence for scheduling rules.  The due-scan query lives here rather than in
the service so the index that makes it fast is declared in one place.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reminder import Reminder, ReminderStatus


class ReminderRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, reminder: Reminder) -> Reminder:
        self._db.add(reminder)
        self._db.flush()
        self._db.refresh(reminder)
        return reminder

    def get_by_id(self, reminder_id: uuid.UUID) -> Optional[Reminder]:
        stmt = select(Reminder).where(Reminder.id == reminder_id)
        return self._db.scalars(stmt).first()

    def list_for_patient(
        self,
        patient_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
        status: Optional[ReminderStatus] = None,
    ) -> List[Reminder]:
        stmt = select(Reminder).where(Reminder.patient_id == patient_id)
        if status is not None:
            stmt = stmt.where(Reminder.status == status)
        stmt = (
            stmt.order_by(Reminder.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def list_for_medication(
        self, medication_id: uuid.UUID
    ) -> List[Reminder]:
        stmt = select(Reminder).where(Reminder.medication_id == medication_id)
        return list(self._db.scalars(stmt).all())

    def list_for_appointment(
        self, appointment_id: uuid.UUID
    ) -> List[Reminder]:
        stmt = select(Reminder).where(Reminder.appointment_id == appointment_id)
        return list(self._db.scalars(stmt).all())

    def find_due(
        self,
        *,
        now: datetime,
        horizon: datetime,
        limit: int = 100,
    ) -> List[Reminder]:
        """
        Active reminders whose next occurrence falls in (now - , horizon].

        Both bounds are needed.  The upper bound keeps a burst of downtime from
        firing a week of reminders at once on recovery; anything that fell out
        of the window is caught by the next occurrence recompute rather than
        being sent late.  The lower bound is inclusive of `now` so a reminder
        due exactly now is picked up.
        """
        stmt = (
            select(Reminder)
            .where(
                Reminder.status == ReminderStatus.active,
                Reminder.next_occurrence_at.isnot(None),
                Reminder.next_occurrence_at >= now,
                Reminder.next_occurrence_at <= horizon,
            )
            .order_by(Reminder.next_occurrence_at.asc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def find_overdue_active(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> List[Reminder]:
        """
        Active reminders whose next occurrence is already in the past.

        These are *not* sent.  A reminder that missed its window is skipped and
        the rule is advanced, because firing a medication reminder hours late
        is worse than not firing it: the dose may already have been taken or
        skipped.  Recovering from scheduler downtime must not produce a burst
        of stale clinical messages.
        """
        stmt = (
            select(Reminder)
            .where(
                Reminder.status == ReminderStatus.active,
                Reminder.next_occurrence_at.isnot(None),
                Reminder.next_occurrence_at < now,
            )
            .order_by(Reminder.next_occurrence_at.asc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def get_by_medication_and_local_time(
        self,
        medication_id: uuid.UUID,
        local_time: object,
    ) -> Optional[Reminder]:
        """
        An existing reminder for this dose slot, if one exists.

        Backs the duplicate check that stops a patient being messaged twice for
        the same dose.  The unique index on (medication_id, local_time) is the
        real guard; this turns the common case into a clean 409 instead of an
        IntegrityError.
        """
        stmt = select(Reminder).where(
            Reminder.medication_id == medication_id,
            Reminder.local_time == local_time,
        )
        return self._db.scalars(stmt).first()

    def update(self, reminder: Reminder, **fields: object) -> Reminder:
        for key, value in fields.items():
            setattr(reminder, key, value)
        self._db.flush()
        self._db.refresh(reminder)
        return reminder

    def bulk_advance(self, reminders: Sequence[Reminder]) -> None:
        """
        Persist recomputed `next_occurrence_at` values in one flush.

        Used by the scheduler after dispatching a batch; a single flush keeps
        the batch to one round trip.
        """
        for reminder in reminders:
            self._db.add(reminder)
        self._db.flush()
