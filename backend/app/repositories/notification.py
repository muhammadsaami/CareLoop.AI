"""
CareLoop AI — Notification Repository (Phase 5)

Persistence for delivery history.

The one method that matters most is `materialize_if_absent`: it is the
idempotency gate.  Two workers that both decide to deliver the same occurrence
produce the same `idempotency_key`; the UNIQUE index turns the loser into a
no-op instead of a second message to a patient.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional, Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationStatus


class NotificationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, notification: Notification) -> Notification:
        self._db.add(notification)
        self._db.flush()
        self._db.refresh(notification)
        return notification

    def get_by_id(self, notification_id: uuid.UUID) -> Optional[Notification]:
        stmt = select(Notification).where(Notification.id == notification_id)
        return self._db.scalars(stmt).first()

    def get_by_idempotency_key(self, key: str) -> Optional[Notification]:
        stmt = select(Notification).where(Notification.idempotency_key == key)
        return self._db.scalars(stmt).first()

    def materialize_if_absent(
        self, notification: Notification
    ) -> tuple[Notification, bool]:
        """
        Insert the occurrence unless its idempotency key already exists.

        Returns:
            `(notification, created)` - `created` is False when an existing row
            was found, meaning this occurrence has already been handled and the
            caller must not deliver it again.

        The pre-check narrows the common case; the IntegrityError catch handles
        the genuine race, where two workers both pass the check.  Catching it
        requires rolling back to a clean transaction state, because Postgres
        aborts the whole transaction on a constraint violation - the
        savepoint (`begin_nested`) keeps the caller's outer transaction usable.
        """
        existing = self.get_by_idempotency_key(notification.idempotency_key)
        if existing is not None:
            return existing, False

        try:
            with self._db.begin_nested():
                self._db.add(notification)
                self._db.flush()
        except IntegrityError:
            # Lost the race.  Another worker owns this occurrence.
            winner = self.get_by_idempotency_key(notification.idempotency_key)
            if winner is not None:
                return winner, False
            raise

        self._db.refresh(notification)
        return notification, True

    def claim_for_delivery(
        self,
        notification_id: uuid.UUID,
        *,
        now: datetime,
    ) -> Optional[Notification]:
        """
        Atomically take ownership of a notification for one delivery attempt.

        A row is eligible when it is either:
          * `pending` - materialised and never attempted, or
          * `failed` with a `next_retry_at` that has elapsed - a transient
            failure that is allowed another attempt.

        Returns None when the row is not claimable: already sent, permanently
        failed, still inside its backoff window, or held by another worker.

        The eligibility test lives in the WHERE clause of a SELECT ... FOR
        UPDATE rather than in Python, so two workers cannot both claim the same
        row: the second blocks on the lock, then re-evaluates the predicate
        against the committed state and finds the row no longer eligible.
        """
        stmt = (
            select(Notification)
            .where(
                Notification.id == notification_id,
                or_(
                    Notification.status == NotificationStatus.pending,
                    and_(
                        Notification.status == NotificationStatus.failed,
                        Notification.next_retry_at.isnot(None),
                        Notification.next_retry_at <= now,
                    ),
                ),
            )
            .with_for_update()
        )
        notification = self._db.scalars(stmt).first()
        if notification is None:
            return None

        notification.status = NotificationStatus.sending
        notification.attempt_count += 1
        notification.updated_at = now
        self._db.flush()
        self._db.refresh(notification)
        return notification

    def reclaim_stale(
        self,
        *,
        stale_before: datetime,
        limit: int = 100,
    ) -> List[Notification]:
        """
        Return notifications stuck in `sending` back to `pending`.

        A worker that is killed mid-send leaves a row in `sending` forever.  On
        the next pass those rows are returned to `pending` so the retry budget
        can take them again.  Bounded by `stale_before` so a delivery that is
        merely slow is not stolen out from under a live worker.
        """
        stmt = (
            select(Notification)
            .where(
                Notification.status == NotificationStatus.sending,
                Notification.updated_at < stale_before,
            )
            .order_by(Notification.updated_at.asc())
            .limit(limit)
        )
        stuck = list(self._db.scalars(stmt).all())
        for notification in stuck:
            notification.status = NotificationStatus.pending
        if stuck:
            self._db.flush()
        return stuck

    def list_for_patient(
        self,
        patient_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
        status: Optional[NotificationStatus] = None,
    ) -> List[Notification]:
        stmt = select(Notification).where(Notification.patient_id == patient_id)
        if status is not None:
            stmt = stmt.where(Notification.status == status)
        stmt = (
            stmt.order_by(Notification.scheduled_for.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def list_pending_due(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> List[Notification]:
        """
        Pending notifications whose scheduled time has arrived.

        Backs the delivery worker.  Ordered oldest-first so a backlog drains in
        the order it accumulated rather than starving the oldest reminder.
        """
        stmt = (
            select(Notification)
            .where(
                Notification.status == NotificationStatus.pending,
                Notification.scheduled_for <= now,
            )
            .order_by(Notification.scheduled_for.asc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def list_for_reminder(
        self,
        reminder_id: uuid.UUID,
        *,
        limit: int = 100,
    ) -> List[Notification]:
        stmt = (
            select(Notification)
            .where(Notification.reminder_id == reminder_id)
            .order_by(Notification.scheduled_for.desc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def find_retryable(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> List[Notification]:
        """
        Failed-for-transient-reason notifications whose backoff has elapsed.

        `status = failed` and a non-null `next_retry_at` is the marker for "this
        failed but is allowed another attempt".  A permanently failed
        notification has `next_retry_at = NULL` and never appears here.
        """
        stmt = (
            select(Notification)
            .where(
                Notification.status == NotificationStatus.failed,
                Notification.next_retry_at.isnot(None),
                Notification.next_retry_at <= now,
            )
            .order_by(Notification.next_retry_at.asc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def update(self, notification: Notification, **fields: object) -> Notification:
        for key, value in fields.items():
            setattr(notification, key, value)
        self._db.flush()
        self._db.refresh(notification)
        return notification

    def bulk_advance(self, notifications: Sequence[Notification]) -> None:
        for notification in notifications:
            self._db.add(notification)
        self._db.flush()
