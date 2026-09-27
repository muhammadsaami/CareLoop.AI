"""
CareLoop AI — Notification Pydantic Schemas (Phase 5)

Notification history is a record of what a patient was actually told, so the
response includes the body.  It is deliberately NOT exposed for bulk listing
through any PHI-minimising path: `NotificationResponse` carries the body, and
the route that returns a list is documented as history access.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.notification import (
    DeliveryChannel,
    NotificationStatus,
    NotificationType,
)


class NotificationResponse(BaseModel):
    """A single notification delivery record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    reminder_id: Optional[uuid.UUID]
    notification_type: NotificationType
    medication_id: Optional[uuid.UUID]
    appointment_id: Optional[uuid.UUID]
    scheduled_for: datetime
    timezone: str
    status: NotificationStatus
    channel: DeliveryChannel
    provider_message_id: Optional[str]
    attempt_count: int
    last_error: Optional[str]
    next_retry_at: Optional[datetime]
    body: str
    recipient: str
    sent_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class NotificationListResponse(BaseModel):
    """Paginated notification history for one patient."""

    patient_id: uuid.UUID
    count: int
    skip: int
    limit: int
    notifications: list[NotificationResponse]


class DispatchRequest(BaseModel):
    """
    Trigger a due-reminder scan manually.

    Exposed so an operator (or a test) can run the scan without a Celery beat
    tick.  Bounded so it cannot be used to hammer the database.

    `extra="forbid"` because a misspelled window override (say
    `lookahead_seconds`) would otherwise be ignored and the operator would get
    a successful response describing a scan they did not ask for.
    """

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(100, ge=1, le=1000)
    look_ahead_seconds: Optional[int] = Field(
        None,
        ge=0,
        le=3600,
        description="Override the configured dispatch window.",
    )


class DispatchResult(BaseModel):
    """Outcome of one due-reminder scan. Counts only - no PHI."""

    scanned: int = Field(
        ..., description="Reminders examined in the due window."
    )
    materialized: int = Field(
        ..., description="New notification occurrences created."
    )
    skipped_duplicate: int = Field(
        ...,
        description=(
            "Occurrences that already existed - the idempotency guarantee "
            "working as designed."
        ),
    )
    skipped_overdue: int = Field(
        ...,
        description="Occurrences past their window; not sent, rule advanced.",
    )
    completed: int = Field(
        ..., description="One-shot reminders marked completed."
    )
    failed: int = Field(
        0,
        description=(
            "Reminders that could not be processed at all. Isolated to their "
            "own savepoint, so these never blocked the rest of the batch."
        ),
    )
