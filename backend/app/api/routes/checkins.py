"""
CareLoop AI — CheckIn Routes

TWO FAMILIES OF ENDPOINT, DELIBERATELY KEPT APART.

Phase 1 (`CheckInCreate` / `CheckInResponse`): the free-text diary entry, with a
caller-supplied `flagged` flag.  Unchanged, and still working.

Phase 6 (everything below the divider): the structured, evaluated daily
check-in.  Coded answers only, no free text, no client-supplied flag, and a
server-computed status.  The two are not merged because merging them would mean
either the evaluated endpoint accepts prose it cannot evaluate, or the legacy
endpoint starts computing clinical state from a field the caller controls.
"""
from __future__ import annotations

import uuid
import datetime as dt
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth_deps import require_patient_access
from app.api.deps import (
    DailyCheckInServiceDep,
    DbSession,
    ReminderServiceDep,
)
from app.schemas.checkin import (
    CheckInCreate,
    CheckInHistoryResponse,
    CheckInQuestionSet,
    CheckInResponse,
    DailyCheckInCreate,
    DailyCheckInResponse,
)
from app.schemas.reminder import CheckInReminderCreate, ReminderResponse
from app.services.checkin import CheckInService
from app.services.patient import PatientService

router = APIRouter(tags=["Check-ins"])


@router.post(
    "/patients/{patient_id}/checkins",
    response_model=CheckInResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a check-in for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def create_checkin(
    patient_id: uuid.UUID, data: CheckInCreate, db: DbSession
) -> CheckInResponse:
    """
    Record a daily check-in response for a patient.

    ``flagged`` and ``flag_reason`` may be provided in Phase 1 for testing.
    Automated AI classification of responses belongs to a later phase.
    """
    return CheckInService(db).create_checkin(patient_id, data)


@router.get(
    "/patients/{patient_id}/checkins",
    response_model=List[CheckInResponse],
    summary="List check-ins for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def list_checkins(patient_id: uuid.UUID, db: DbSession) -> List[CheckInResponse]:
    return CheckInService(db).list_checkins(patient_id)


@router.get(
    "/checkins/{checkin_id}",
    response_model=CheckInResponse,
    summary="Get a check-in by ID",
    responses={404: {"description": "Check-in not found"}},
    dependencies=[Depends(require_patient_access)],
)
def get_checkin(checkin_id: uuid.UUID, db: DbSession) -> CheckInResponse:
    return CheckInService(db).get_checkin(checkin_id)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6 — structured daily check-ins
# ═══════════════════════════════════════════════════════════════════════════


@router.get(
    "/patients/{patient_id}/checkins/questions",
    response_model=CheckInQuestionSet,
    summary="Get the check-in question set for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def get_checkin_questions(
    patient_id: uuid.UUID, db: DbSession, service: DailyCheckInServiceDep
) -> CheckInQuestionSet:
    """
    The configurable question set, plus this patient's own warning symptoms.

    Returned as data so a client never has to hard-code the question keys or
    invent its own prompts.  The warning-symptom options are the patient's own
    stored rows, filtered by patient at the query, so a patient can only ever
    be offered symptoms that are on their record.
    """
    patient = PatientService(db).get_patient(patient_id)
    return service.questions_for_patient(patient)


@router.post(
    "/patients/{patient_id}/checkins/daily",
    response_model=DailyCheckInResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit today's structured check-in and evaluate it",
    responses={
        404: {"description": "Patient not found"},
        409: {"description": "A check-in is already recorded for this date"},
        422: {
            "description": (
                "The answers were rejected - no answers at all, a timezone "
                "that is not the patient's, a future date, or too many "
                "symptom reports"
            )
        },
    },
    dependencies=[Depends(require_patient_access)],
)
def submit_daily_checkin(
    patient_id: uuid.UUID,
    data: DailyCheckInCreate,
    db: DbSession,
    service: DailyCheckInServiceDep,
) -> DailyCheckInResponse:
    """
    Record and evaluate one day's structured check-in.

    The request carries CODED answers only.  There is no free-text field, and
    no `flagged` flag: a client cannot assert its own clinical conclusion, and
    the returned `status` is computed by the published rule set from stored
    facts.

    Returns 409 if this patient already has a check-in for the date.  A
    re-submission is refused rather than overwriting the first answer - the
    record of what the patient actually said is the thing being protected.
    """
    patient = PatientService(db).get_patient(patient_id)
    return service.submit(patient, data)


@router.get(
    "/patients/{patient_id}/checkins/daily/latest",
    response_model=Optional[DailyCheckInResponse],
    summary="Get the most recent structured check-in",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def get_latest_daily_checkin(
    patient_id: uuid.UUID, db: DbSession, service: DailyCheckInServiceDep
) -> Optional[DailyCheckInResponse]:
    """
    The most recent structured check-in, or null.

    Null rather than 404: having not checked in yet is the normal state on day
    one, not a missing resource.
    """
    PatientService(db).get_patient(patient_id)
    return service.get_latest(patient_id)


@router.get(
    "/patients/{patient_id}/checkins/daily/{checkin_id}",
    response_model=DailyCheckInResponse,
    summary="Get one structured check-in by ID",
    responses={404: {"description": "Check-in not found for this patient"}},
    dependencies=[Depends(require_patient_access)],
)
def get_daily_checkin(
    patient_id: uuid.UUID,
    checkin_id: uuid.UUID,
    db: DbSession,
    service: DailyCheckInServiceDep,
) -> DailyCheckInResponse:
    """
    One structured check-in, addressed through the patient who owns it.

    Scoped to the path's patient: a check-in id belonging to another patient
    answers 404, the same as an id that does not exist, so this endpoint cannot
    be used to discover another patient's check-ins.
    """
    PatientService(db).get_patient(patient_id)
    return service.get_for_patient(patient_id, checkin_id)


@router.get(
    "/patients/{patient_id}/checkins/daily",
    response_model=CheckInHistoryResponse,
    summary="Paged history of structured check-ins",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def get_daily_checkin_history(
    patient_id: uuid.UUID,
    db: DbSession,
    service: DailyCheckInServiceDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    start_date: Optional[dt.date] = None,
    end_date: Optional[dt.date] = None,
) -> CheckInHistoryResponse:
    """
    Structured check-in history, newest first.

    `start_date` and `end_date` are inclusive and applied in SQL.  The page
    carries the total so a client can show "showing 20 of 84" without a second
    request.
    """
    PatientService(db).get_patient(patient_id)
    if (
        start_date is not None
        and end_date is not None
        and start_date > end_date
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date cannot be after end_date.",
        )
    total, items = service.history(
        patient_id,
        skip=skip,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
    )
    return CheckInHistoryResponse(
        total=total, skip=skip, limit=limit, items=items
    )


@router.post(
    "/patients/{patient_id}/checkins/schedule",
    response_model=ReminderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Set up (or fetch) the daily check-in prompt",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def schedule_daily_checkin(
    patient_id: uuid.UUID,
    data: CheckInReminderCreate,
    db: DbSession,
    reminders: ReminderServiceDep,
) -> ReminderResponse:
    """
    Schedule the daily prompt that asks the patient to complete a check-in.

    The prompt is an ordinary Phase 5 `Reminder`, so the existing scheduler
    sends it and the existing retry policy covers a failed send.  No second
    scheduling mechanism is introduced.

    Idempotent: calling this again for the same local time returns the existing
    prompt rather than messaging the patient twice a day.  Use
    `DELETE /patients/{id}/reminders/{reminder_id}` to stop it, then create a
    new one at a different time.
    """
    PatientService(db).get_patient(patient_id)
    reminder = reminders.create_checkin_reminder(patient_id, data)
    return ReminderResponse.model_validate(reminder)
