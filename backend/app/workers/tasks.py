"""
CareLoop AI — Celery Tasks (Phase 5)

Each task is a thin, idempotent wrapper around a service.  The logic lives in
the service so that (a) it is testable without a broker, and (b) the API and the
worker can never disagree about the rules.

Every task is safe to run twice, for the reasons in `celery_app.py`: Celery
delivers at-least-once, and an operator can trigger a dispatch by hand while a
beat tick is already running.  Idempotency is enforced by a deterministic
idempotency key under a UNIQUE index, so a duplicate run is a no-op rather than
a second message to a patient.

Task arguments are UUID strings, never ORM objects: a task payload may be
serialised, held in Redis, and re-read minutes later, so it must not capture a
detached or stale database row.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import log_exception_without_phi
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="careloop.dispatch_due_reminders", bind=True)
def dispatch_due_reminders(self, limit: int = 100) -> Dict[str, Any]:
    """
    Materialise notifications for every reminder currently due.

    Idempotent: a reminder already materialised for this occurrence is counted
    in `skipped_duplicate` and not re-sent.
    """
    from app.services.scheduler import SchedulerService

    session: Session = SessionLocal()
    try:
        service = SchedulerService(session)
        result = service.dispatch_due(limit=limit)
        session.commit()
        return result.model_dump()
    except Exception as exc:
        session.rollback()
        # Counts are absent on failure; the exception is what matters, and it
        # is logged by type and task name only.
        log_exception_without_phi(
            logger, "task_failed", exc, task=self.name
        )
        raise
    finally:
        session.close()


@celery_app.task(name="careloop.deliver_pending_notifications", bind=True)
def deliver_pending_notifications(self, limit: int = 100) -> Dict[str, Any]:
    """
    Send notifications that are due and still pending.

    Idempotent: a notification already sent is not claimable, so it is skipped
    rather than sent twice.
    """
    from app.services.scheduler import SchedulerService

    session: Session = SessionLocal()
    try:
        service = SchedulerService(session)
        result = service.deliver_pending(limit=limit)
        session.commit()
        return result
    except Exception as exc:
        session.rollback()
        log_exception_without_phi(
            logger, "task_failed", exc, task=self.name
        )
        raise
    finally:
        session.close()


@celery_app.task(name="careloop.send_notification", bind=True)
def send_notification(self, notification_id: str) -> Dict[str, Any]:
    """
    Deliver one notification by id.

    Used both by the batch task and by a manual retry.  A notification that is
    already terminal is returned unchanged, so a redelivered task is a no-op.
    """
    from app.services.notification import NotificationService

    session: Session = SessionLocal()
    try:
        service = NotificationService(session)
        notification = service.deliver(uuid.UUID(str(notification_id)))
        return {
            "notification_id": str(notification.id),
            "status": notification.status.value,
            "attempt_count": notification.attempt_count,
        }
    except Exception as exc:
        session.rollback()
        log_exception_without_phi(
            logger, "task_failed", exc, task=self.name
        )
        raise
    finally:
        session.close()


@celery_app.task(name="careloop.retry_failed_notifications", bind=True)
def retry_failed_notifications(self, limit: int = 100) -> Dict[str, Any]:
    """
    Re-attempt notifications whose backoff window has elapsed.

    Idempotent: a row already sent is not claimable, so a repeated run does not
    duplicate a message.
    """
    from app.services.notification import NotificationService

    session: Session = SessionLocal()
    try:
        service = NotificationService(session)
        result = service.retry_due(limit=limit)
        session.commit()
        return result
    except Exception as exc:
        session.rollback()
        log_exception_without_phi(
            logger, "task_failed", exc, task=self.name
        )
        raise
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6 — escalation notifications
# ═══════════════════════════════════════════════════════════════════════════


@celery_app.task(name="careloop.notify_pending_escalations", bind=True)
def notify_pending_escalations(self, limit: int = 100) -> Dict[str, Any]:
    """
    Build the caregiver notification for every escalation that still needs one.

    This task only MATERIALISES notifications; it does not send them.  Delivery
    belongs to `careloop.deliver_pending_notifications`, which already picks up
    any `pending` notification whose scheduled time has arrived - including
    these.  That is the whole reason Phase 6 adds one task rather than two: a
    second send path would be a second retry policy and a second place for a
    duplicate alert to be born.

    WHY A TASK EXISTS AT ALL, WHEN SUBMISSION ALREADY REQUESTS NOTIFICATION
    A check-in submission asks for its escalation's notification inline, so the
    common case is handled during the request.  This task covers the cases that
    are not: a submission that failed between committing the escalation and
    materialising the notification, a worker that died in that window, and a
    caregiver contact added to a patient record after the fact.  Without it,
    those escalations would sit in `pending` indefinitely with nothing left to
    pick them up.

    IDEMPOTENT
    An escalation that already has a notification is excluded by the query, and
    the notification key is derived from the escalation id, so a repeated run
    cannot produce a second alert about one event.

    A failure on one escalation is rolled back individually and the batch
    continues: one patient's bad row must not stop the other patients in the
    same batch from being notified.
    """
    from app.services.escalation import EscalationService

    session: Session = SessionLocal()
    try:
        service = EscalationService(session)
        result = service.notify_pending(limit=limit)
        session.commit()
        return result
    except Exception as exc:
        session.rollback()
        log_exception_without_phi(
            logger, "task_failed", exc, task=self.name
        )
        raise
    finally:
        session.close()


@celery_app.task(name="careloop.reconcile_scheduler", bind=True)
def reconcile_scheduler(self, limit: int = 100) -> Dict[str, Any]:
    """
    Housekeeping: skip overdue occurrences, reclaim orphaned sends.

    Overdue occurrences are retired WITHOUT being sent.  A medication reminder
    that fires hours late is a safety problem - the dose has probably already
    been taken or deliberately skipped, and a late prompt risks a double dose.
    """
    from app.services.scheduler import SchedulerService

    session: Session = SessionLocal()
    try:
        service = SchedulerService(session)
        result = service.reconcile(limit=limit)
        session.commit()
        return result
    except Exception as exc:
        session.rollback()
        log_exception_without_phi(
            logger, "task_failed", exc, task=self.name
        )
        raise
    finally:
        session.close()
