"""
CareLoop AI — Scheduler Service (Phase 5)

The recurring-job engine.  A periodic Celery beat task calls `dispatch_due`,
which finds reminders whose next occurrence has arrived, materialises one
notification per occurrence, and advances the rule.

Two decisions are worth stating, because both are the difference between a
helpful reminder and a harmful one.

1. Overdue occurrences are NOT sent.
   If the scheduler was down for an hour, firing a medication reminder an hour
   late is worse than silence: the patient has probably already taken the dose,
   or deliberately skipped it, and a late prompt can cause a double dose.  So an
   overdue occurrence is skipped, the rule is advanced to its next real
   occurrence, and the skip is recorded.  A similar upper bound on the due
   window stops a long outage from producing a burst on recovery.

2. Advancing happens in the same transaction as materialising.
   If the process dies between the two, the retry finds the existing
   notification via its idempotency key and skips it, so the pair is safe to
   re-run.  Advancing first would instead risk skipping a dose entirely.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import log_exception_without_phi
from app.core.timezones import utcnow
from app.models.notification import NotificationStatus
from app.models.reminder import Reminder, ReminderStatus, ReminderType
from app.repositories.reminder import ReminderRepository
from app.repositories.notification import NotificationRepository
from app.schemas.notification import DispatchResult
from app.services.notification import NotificationService
from app.services.reminder import ReminderService

logger = logging.getLogger(__name__)


class SchedulerService:
    """Finds due reminders and materialises their notifications."""

    #: Upper bound on how many occurrences one catch-up pass will walk.  A daily
    #: rule gets roughly 13 months of headroom; a far older outage simply
    #: finishes catching up on the next scheduled pass.
    _MAX_CATCH_UP_STEPS = 400

    def __init__(
        self,
        db: Session,
        *,
        settings: Optional[Settings] = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._reminders = ReminderRepository(db)
        self._notifications = NotificationRepository(db)
        self._notification_service = NotificationService(
            db, settings=self._settings
        )
        self._reminder_service = ReminderService(db)

    # ── Main entry point ────────────────────────────────────────────────────
    def dispatch_due(
        self,
        *,
        now: Optional[datetime] = None,
        limit: Optional[int] = None,
        look_ahead_seconds: Optional[int] = None,
    ) -> DispatchResult:
        """
        Materialise every reminder due within the dispatch window.

        Idempotent: running it twice over the same window creates no second
        notification for any occurrence.
        """
        reference = now or utcnow()
        batch_size = limit or self._settings.scheduler_batch_size
        window = (
            look_ahead_seconds
            if look_ahead_seconds is not None
            else self._settings.scheduler_lookahead_seconds
        )
        horizon = reference + timedelta(seconds=window)

        due = self._reminders.find_due(
            now=reference, horizon=horizon, limit=batch_size
        )

        result = DispatchResult(
            scanned=len(due),
            materialized=0,
            skipped_duplicate=0,
            skipped_overdue=0,
            completed=0,
        )

        for reminder in due:
            self._process(reminder, result, reference=reference)

        # Public entry points own their transaction, so the batch is atomic:
        # either every processed reminder is persisted or none is.  The API
        # route and the Celery task both rely on this.
        self._db.commit()

        # Counts only - no PHI in the log line.
        logger.info(
            "scheduler_dispatch scanned=%d materialized=%d duplicates=%d "
            "overdue=%d failed=%d completed=%d",
            result.scanned,
            result.materialized,
            result.skipped_duplicate,
            result.skipped_overdue,
            result.failed,
            result.completed,
        )
        return result

    def _process(
        self,
        reminder: Reminder,
        result: DispatchResult,
        *,
        reference: datetime,
    ) -> None:
        """
        Materialise one reminder's due occurrence and advance the rule.

        Runs inside a SAVEPOINT so a single bad reminder cannot take the batch
        down with it.  This matters for more than tidiness: a failed `flush()`
        leaves the PostgreSQL transaction in an aborted state, so without a
        savepoint every *other* patient's reminder in the same batch would fail
        too.  Rolling back to the savepoint restores a usable transaction.
        """
        occurrence_at = reminder.next_occurrence_at
        if occurrence_at is None:
            return

        one_shot = (
            reminder.reminder_type == ReminderType.appointment
            or reminder.recurrence.value == "none"
        )

        savepoint = self._db.begin_nested()
        try:
            _, created = self._notification_service.materialize(
                reminder, occurrence_at=occurrence_at
            )
            if created:
                result.materialized += 1
            else:
                result.skipped_duplicate += 1

            if self._advance(reminder, after=occurrence_at, one_shot=one_shot):
                result.completed += 1
            savepoint.commit()
        except Exception as exc:
            savepoint.rollback()
            result.failed += 1
            # Log the id only - never the body or a vendor payload.
            log_exception_without_phi(
                logger,
                "scheduler_materialize_failed",
                exc,
                reminder_id=reminder.id,
                occurrence_at=occurrence_at.isoformat(),
            )
            # Still advance the rule so one permanently broken reminder is not
            # retried on every single tick.  Advancing from `occurrence_at`
            # (not from now) guarantees forward progress: computing from `now`
            # could land on the very occurrence that just failed, because a
            # future occurrence inside the look-ahead window sits ahead of now.
            self._advance_after_failure(
                reminder, occurrence_at=occurrence_at, one_shot=one_shot, result=result
            )

    def _advance_after_failure(
        self,
        reminder: Reminder,
        *,
        occurrence_at: datetime,
        one_shot: bool,
        result: DispatchResult,
    ) -> None:
        """
        Best-effort advancement after a failed materialisation.

        Wrapped in its own savepoint, and failure is swallowed: the rule may be
        in a state we cannot reason about, and losing one rule is far better
        than aborting the batch that is still in flight.
        """
        savepoint = self._db.begin_nested()
        try:
            if self._advance(
                reminder, after=occurrence_at, one_shot=one_shot
            ):
                result.completed += 1
            savepoint.commit()
        except Exception as exc:
            savepoint.rollback()
            log_exception_without_phi(
                logger,
                "scheduler_advance_failed",
                exc,
                reminder_id=reminder.id,
            )

    def _advance(
        self,
        reminder: Reminder,
        *,
        after: datetime,
        one_shot: bool,
    ) -> bool:
        """
        Move the rule to its next occurrence, or retire it.

        Advancing from `after = occurrence_at` (not from now) means a reminder
        that was due a moment ago computes the occurrence after the one just
        handled, rather than jumping an extra day ahead.

        Returns True when the rule was retired by this call.
        """
        if one_shot:
            reminder.status = ReminderStatus.completed
            reminder.next_occurrence_at = None
            self._db.add(reminder)
            self._db.flush()
            return True

        # Recurring medication rule: the next occurrence strictly after the one
        # just handled.  `_clamp_to_window` returns None once the rule has run
        # past `active_until`, which retires it.
        next_at = self._next_after(reminder, after)

        if next_at is None:
            reminder.status = ReminderStatus.completed
            reminder.next_occurrence_at = None
            self._db.add(reminder)
            self._db.flush()
            return True

        reminder.next_occurrence_at = next_at
        self._db.add(reminder)
        self._db.flush()
        return False

    def _next_after(
        self, reminder: Reminder, after: datetime
    ) -> Optional[datetime]:
        """
        The occurrence after `after`, for a recurring medication reminder.

        Delegates to the reminder service so there is exactly one
        implementation of the DST-correct recurrence maths.
        """
        if reminder.local_time is None:
            return None
        service = self._reminder_service
        instant = service._compute_next(
            after,
            reminder.timezone,
            reminder.local_time,
            reminder.recurrence,
            reminder.recurrence_interval,
        )
        return service._clamp_to_window(
            instant, reminder.active_from, reminder.active_until
        )

    # ── Overdue handling ────────────────────────────────────────────────────
    def skip_overdue(
        self,
        *,
        now: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> int:
        """
        Retire overdue occurrences without sending them.

        See the module docstring: a late medication prompt is a safety problem,
        not a helpful one.  Returns how many rules were caught up.
        """
        reference = now or utcnow()
        batch_size = limit or self._settings.scheduler_batch_size
        overdue = self._reminders.find_overdue_active(
            now=reference, limit=batch_size
        )
        if not overdue:
            return 0

        for reminder in overdue:
            one_shot = (
                reminder.reminder_type == ReminderType.appointment
                or reminder.recurrence.value == "none"
            )
            self._catch_up(reminder, until=reference, one_shot=one_shot)

        self._db.commit()
        logger.info(
            "scheduler_overdue_skipped count=%d", len(overdue)
        )
        return len(overdue)

    def _catch_up(
        self,
        reminder: Reminder,
        *,
        until: datetime,
        one_shot: bool,
    ) -> bool:
        """
        Advance a stale rule past every occurrence at or before `until`.

        Advancing only one occurrence per pass would leave a rule that fell
        behind (a long outage) still overdue, so it would be re-examined - and
        re-counted as skipped - on every subsequent tick until it eventually
        caught up one day at a time.  Catching up fully in a single pass makes
        reconciliation idempotent and keeps the due scan clean.

        Bounded by `_MAX_CATCH_UP_STEPS` so a pathological rule (an interval of
        zero, or a very old `active_from`) cannot spin here; if the cap is hit
        the rule is simply advanced that far and finishes catching up on the
        next pass.

        Returns True when the rule was retired.
        """
        retired = False
        for _ in range(self._MAX_CATCH_UP_STEPS):
            current = reminder.next_occurrence_at
            if current is None:
                retired = True
                break

            if current > until:
                break

            if self._advance(reminder, after=current, one_shot=one_shot):
                retired = True
                break

        return retired

    # ── Delivery ────────────────────────────────────────────────────────────
    def deliver_pending(
        self,
        *,
        limit: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> dict[str, int]:
        """
        Deliver notifications that are due and still pending.

        Separate from `dispatch_due` so the two can run on different cadences:
        creating a record and calling a vendor should not share a lock or a
        latency budget.
        """
        reference = now or utcnow()
        batch_size = limit or self._settings.scheduler_batch_size

        pending = self._notifications.list_pending_due(
            now=reference, limit=batch_size
        )

        sent = failed = 0
        for notification in pending:
            outcome = self._notification_service.deliver(notification.id)
            if outcome.status == NotificationStatus.sent:
                sent += 1
            else:
                failed += 1
        return {"examined": len(pending), "sent": sent, "failed": failed}

    # ── Maintenance ─────────────────────────────────────────────────────────
    def reconcile(
        self,
        *,
        now: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> dict[str, int]:
        """Skip overdue occurrences and reclaim orphaned sends."""
        reference = now or utcnow()
        skipped = self.skip_overdue(now=reference, limit=limit)
        reclaimed = self._notification_service.reclaim_stale(limit=limit)
        return {"overdue_skipped": skipped, "reclaimed": reclaimed}
