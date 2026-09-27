"""
CareLoop AI — Celery Workers (Phase 5)

The recurring-job backend.  Importing this package does NOT contact Redis; the
broker is only used when a worker process actually starts or a task is
published, so the API process never depends on it being up.

Run a worker:
    celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
    celery -A app.workers.celery_app:celery_app worker --beat --loglevel=INFO

`--beat` is required for the recurring schedule; without it only manually
published tasks run.
"""
from app.workers.celery_app import celery_app

# Importing the task module registers every task with the app.  Without this,
# `dispatch_due_reminders.delay(...)` would fail with an unregistered-task
# error, because decorators alone do not load the module.
from app.workers import tasks  # noqa: F401  (imported for side effects)

__all__ = ["celery_app", "tasks"]
