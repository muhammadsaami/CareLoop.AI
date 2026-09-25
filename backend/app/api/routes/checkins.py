"""
CareLoop AI — CheckIn Routes
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, status

from app.api.deps import DbSession
from app.schemas.checkin import CheckInCreate, CheckInResponse
from app.services.checkin import CheckInService

router = APIRouter(tags=["Check-ins"])


@router.post(
    "/patients/{patient_id}/checkins",
    response_model=CheckInResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a check-in for a patient",
    responses={404: {"description": "Patient not found"}},
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
)
def list_checkins(patient_id: uuid.UUID, db: DbSession) -> List[CheckInResponse]:
    return CheckInService(db).list_checkins(patient_id)


@router.get(
    "/checkins/{checkin_id}",
    response_model=CheckInResponse,
    summary="Get a check-in by ID",
    responses={404: {"description": "Check-in not found"}},
)
def get_checkin(checkin_id: uuid.UUID, db: DbSession) -> CheckInResponse:
    return CheckInService(db).get_checkin(checkin_id)
