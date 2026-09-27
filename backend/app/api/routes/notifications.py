"""
CareLoop AI — Notification API Routes (Phase 5)

Notification history and manual dispatch.  Delivery itself happens in a worker;
these routes read the record and let an operator trigger a scan.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Query

from app.api.deps import (
    NotificationServiceDep,
    ReminderServiceDep,
    SchedulerServiceDep,
)
from app.models.notification import NotificationStatus
from app.schemas.notification import (
    DispatchRequest,
    DispatchResult,
    NotificationListResponse,
    NotificationResponse,
)

router = APIRouter(tags=["notifications"])


@router.get(
    "/patients/{patient_id}/notifications",
    response_model=NotificationListResponse,
    summary="Notification history for a patient",
)
def notification_history(
    patient_id: uuid.UUID,
    service: NotificationServiceDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    notification_status: Optional[NotificationStatus] = Query(
        None,
        alias="status",
        description="Filter by delivery status, e.g. 'failed'.",
    ),
) -> NotificationListResponse:
    """
    A patient's delivery history, newest first.

    Includes the rendered body: this endpoint is the record of what the patient
    was actually told, which is the point of a notification log.  It carries no
    document content and no extraction output.
    """
    notifications = service.history_for_patient(
        patient_id, skip=skip, limit=limit, status=notification_status
    )
    return NotificationListResponse(
        patient_id=patient_id,
        count=len(notifications),
        skip=skip,
        limit=limit,
        notifications=[
            NotificationResponse.model_validate(n) for n in notifications
        ],
    )


@router.get(
    "/patients/{patient_id}/reminders/{reminder_id}/notifications",
    response_model=NotificationListResponse,
    summary="Delivery history for one reminder",
)
def reminder_history(
    patient_id: uuid.UUID,
    reminder_id: uuid.UUID,
    service: NotificationServiceDep,
    reminders: ReminderServiceDep,
) -> NotificationListResponse:
    """
    Every occurrence materialised for one reminder.

    This is the audit view for a single rule - useful when a patient reports
    they were not reminded, or were reminded twice.

    The reminder is scoped to the patient: these rows carry the rendered body,
    so a reminder belonging to someone else must be indistinguishable from one
    that does not exist.  Same reasoning as `get_reminder`.
    """
    reminder = reminders.get(reminder_id)
    if reminder.patient_id != patient_id:
        # Same shape as ReminderNotFoundError's default message, so the two
        # cases cannot be told apart by an id-probing caller.
        from app.core.exceptions import ReminderNotFoundError

        raise ReminderNotFoundError()

    notifications = service.history_for_reminder(reminder_id)
    return NotificationListResponse(
        patient_id=patient_id,
        count=len(notifications),
        skip=0,
        limit=len(notifications),
        notifications=[
            NotificationResponse.model_validate(n) for n in notifications
        ],
    )


@router.get(
    "/notifications/{notification_id}",
    response_model=NotificationResponse,
    summary="Fetch one notification record",
)
def get_notification(
    notification_id: uuid.UUID,
    service: NotificationServiceDep,
) -> NotificationResponse:
    """Fetch a single delivery record, including its retry count and error."""
    return NotificationResponse.model_validate(service.get(notification_id))


@router.post(
    "/notifications/dispatch",
    response_model=DispatchResult,
    summary="Run a due-reminder scan now",
)
def dispatch_due_reminders(
    payload: DispatchRequest,
    service: SchedulerServiceDep,
) -> DispatchResult:
    """
    Materialise notifications for every reminder currently due.

    Exposed so a scan can be triggered without waiting for a beat tick - for an
    operator, or a test.  Idempotent: re-running it over the same window
    creates no duplicate notifications, and `skipped_duplicate` reports how many
    already existed.
    """
    result = service.dispatch_due(
        limit=payload.limit,
        look_ahead_seconds=payload.look_ahead_seconds,
    )
    return result


@router.post(
    "/notifications/retry",
    summary="Retry notifications whose backoff has elapsed",
)
def retry_notifications(
    service: NotificationServiceDep,
    limit: int = Query(100, ge=1, le=1000),
) -> dict:
    """
    Re-attempt transiently failed notifications.

    Returns counts only.  Each retry recomputes the same idempotency key as the
    original attempt, so a retry can never produce a second message.
    """
    return service.retry_due(limit=limit)
