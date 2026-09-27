"""
CareLoop AI — Celery Application (Phase 5)

The broker and result backend are Redis.  Nothing here is contacted at import
time by the API process: `celery_app.py` is imported by the worker entry point,
not by `app.main`, so the API boots with no broker available.

TASK IDEMPOTENCY IS THE POINT
Every task here is safe to run twice.  Celery delivers at-least-once, so a
worker killed after sending but before acknowledging will re-run the task, and
an operator will occasionally run a dispatch by hand while a beat tick is
already in flight.  None of those cases may produce two messages to a patient.

The guarantee comes from the database, not from Celery: every task delegates to
a service whose write is keyed on a deterministic idempotency key under a UNIQUE
index.  A duplicate run finds the existing row and returns it.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

settings = get_settings()

#: The Celery application.  Name is qualified so it is identifiable in
#: `celery -A` listings and in Flower.
celery_app = Celery(
    "careloop",
    broker=settings.celery_broker_url or settings.redis_url,
    backend=settings.celery_result_backend or settings.redis_url,
)

celery_app.conf.update(
    # ── Serialisation ──────────────────────────────────────────────────────
    # JSON, not pickle.  A broker is reachable infrastructure; a pickled task
    # payload is an arbitrary-code-execution vector.  Every task argument here
    # is a string UUID, which JSON carries losslessly.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # ── Delivery semantics ─────────────────────────────────────────────────
    # Acknowledge only after the task returns.  Combined with the idempotency
    # key, a crash causes a re-run that is a no-op rather than a lost reminder.
    task_acks_late=True,
    # Reclaim a task from a worker that died without acknowledging it, so a
    # reminder is not silently lost when a worker is killed mid-run.
    task_reject_on_worker_lost=True,
    # ── Timing ─────────────────────────────────────────────────────────────
    timezone="UTC",
    enable_utc=True,
    task_time_limit=60,
    task_soft_time_limit=45,
    # ── Routing ────────────────────────────────────────────────────────────
    task_default_queue=settings.celery_queue,
    # With a single worker and long tasks, prefetching several at once means a
    # redelivered task waits behind the whole batch.
    worker_prefetch_multiplier=1,
    # Discard task return values.  The tasks return count dicts purely so they
    # are testable and so a manual `celery call` prints something useful; no
    # caller reads them, and the outcome that matters lives in the database.
    # Keeping them would write a result to Redis every minute, per task,
    # forever - unbounded growth for data nobody queries.  Idempotency must
    # never depend on the result backend, and this is what guarantees it.
    task_ignore_result=True,
)

# ── Beat schedule ────────────────────────────────────────────────────────────
#
# Three jobs on separate cadences, because they have different costs and
# different tolerances:
#
#   * `dispatch_due_reminders` - the scheduler tick. 60s is well inside the
#     300s lookahead window, so an occurrence is materialised with time to
#     spare before it is due.
#   * `deliver_pending_notifications` - 30s, faster than dispatch, so a
#     materialised notification is sent promptly.
#   * `reconcile_scheduler` - 5 min housekeeping: skip overdue occurrences and
#     reclaim notifications orphaned by a dead worker.
celery_app.conf.beat_schedule = {
    "dispatch-due-reminders": {
        "task": "careloop.dispatch_due_reminders",
        "schedule": crontab(),  # every minute
    },
    "deliver-pending-notifications": {
        "task": "careloop.deliver_pending_notifications",
        "schedule": crontab(),  # every minute
    },
    "reconcile-scheduler": {
        "task": "careloop.reconcile_scheduler",
        "schedule": 300.0,  # every 5 minutes
    },
}

logger.debug(
    "celery_configured queue=%s broker_configured=%s",
    settings.celery_queue,
    bool(settings.celery_broker_url or settings.redis_url),
)

__all__ = ["celery_app", "settings"]
