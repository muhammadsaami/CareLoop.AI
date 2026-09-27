"""
CareLoop AI — Reminder API Routes (Phase 5)

Creating a reminder means attaching a schedule to structured data that already
exists.  Two routes, because the two kinds of schedule have different shapes and
different safety rules:

  POST /patients/{id}/reminders/medication
      Requires explicit dose TIMES.  There is no field for a free-text
      schedule, because the server must not interpret one.

  POST /patients/{id}/reminders/appointment
      Requires only a LEAD TIME.  The appointment's own instant already exists
      on the appointment row and is read from the database; accepting a second
      copy here would let the two disagree.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Query, Response, status

from app.api.deps import ReminderServiceDep
from app.models.reminder import ReminderStatus
from app.schemas.reminder import (
    AppointmentReminderCreate,
    MedicationReminderCreate,
    ReminderResponse,
    ReminderUpdate,
)

router = APIRouter(prefix="/patients", tags=["reminders"])


@router.post(
    "/{patient_id}/reminders/medication",
    response_model=list[ReminderResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create recurring medication reminders from explicit dose times",
)
def create_medication_reminders(
    patient_id: uuid.UUID,
    payload: MedicationReminderCreate,
    service: ReminderServiceDep,
    response: Response,
) -> list[ReminderResponse]:
    """
    Create one reminder per supplied dose time.

    A medication prescribed three times a day yields three reminder rules, each
    independently pausable, rather than one rule with three times.
    """
    reminders = service.create_medication_reminder(patient_id, payload)
    return [ReminderResponse.model_validate(r) for r in reminders]


@router.post(
    "/{patient_id}/reminders/appointment",
    response_model=ReminderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a one-shot reminder ahead of an existing appointment",
)
def create_appointment_reminder(
    patient_id: uuid.UUID,
    payload: AppointmentReminderCreate,
    service: ReminderServiceDep,
) -> ReminderResponse:
    """
    Remind the patient `lead_time_minutes` before an existing appointment.

    422 if that instant has already passed: a reminder that would fire
    immediately is never what the caller meant.
    """
    reminder = service.create_appointment_reminder(patient_id, payload)
    return ReminderResponse.model_validate(reminder)


@router.get(
    "/{patient_id}/reminders",
    response_model=list[ReminderResponse],
    summary="List a patient's reminders",
)
def list_reminders(
    patient_id: uuid.UUID,
    service: ReminderServiceDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    reminder_status: Optional[ReminderStatus] = Query(
        None, alias="status", description="Filter by lifecycle status."
    ),
) -> list[ReminderResponse]:
    """List reminders, newest first, optionally filtered by status."""
    reminders = service.list_for_patient(
        patient_id, skip=skip, limit=limit, status=reminder_status
    )
    return [ReminderResponse.model_validate(r) for r in reminders]


@router.get(
    "/{patient_id}/reminders/{reminder_id}",
    response_model=ReminderResponse,
    summary="Fetch one reminder",
)
def get_reminder(
    patient_id: uuid.UUID,
    reminder_id: uuid.UUID,
    service: ReminderServiceDep,
) -> ReminderResponse:
    """
    Fetch a single reminder, scoped to the patient.

    A reminder belonging to a different patient is reported as 404, the same as
    a reminder that does not exist, so the id space cannot be probed.
    """
    reminder = service.get(reminder_id)
    if reminder.patient_id != patient_id:
        # Same shape as ReminderNotFoundError's default message.
        from app.core.exceptions import ReminderNotFoundError

        raise ReminderNotFoundError()
    return ReminderResponse.model_validate(reminder)


@router.patch(
    "/{patient_id}/reminders/{reminder_id}",
    response_model=ReminderResponse,
    summary="Update a reminder's lifecycle or active window",
)
def update_reminder(
    patient_id: uuid.UUID,
    reminder_id: uuid.UUID,
    payload: ReminderUpdate,
    service: ReminderServiceDep,
) -> ReminderResponse:
    """
    Pause, resume, or re-window a reminder.

    The dose time and source references are not editable: changing the time of
    a live rule in place would silently re-point reminders the patient has
    already received.  Cancel and create a new reminder instead.
    """
    reminder = service.get(reminder_id)
    if reminder.patient_id != patient_id:
        from app.core.exceptions import ReminderNotFoundError

        raise ReminderNotFoundError()
    updated = service.update(reminder_id, payload)
    return ReminderResponse.model_validate(updated)


@router.delete(
    "/{patient_id}/reminders/{reminder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a reminder",
)
def cancel_reminder(
    patient_id: uuid.UUID,
    reminder_id: uuid.UUID,
    service: ReminderServiceDep,
) -> Response:
    """
    Cancel a reminder.

    The row is retained rather than deleted so that a notification already
    delivered can always be traced back to the rule that produced it.
    """
    reminder = service.get(reminder_id)
    if reminder.patient_id != patient_id:
        from app.core.exceptions import ReminderNotFoundError

        raise ReminderNotFoundError()
    service.cancel(reminder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
