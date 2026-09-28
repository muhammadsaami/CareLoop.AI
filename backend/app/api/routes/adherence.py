"""
CareLoop AI — Adherence Routes
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, status

from app.api.auth_deps import require_patient_access
from app.api.deps import DbSession
from app.schemas.adherence_log import AdherenceLogCreate, AdherenceLogResponse, AdherenceLogUpdate
from app.services.adherence import AdherenceService

router = APIRouter(tags=["Adherence"])


@router.post(
    "/patients/{patient_id}/adherence",
    response_model=AdherenceLogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an adherence log entry",
    responses={
        404: {"description": "Patient or medication not found"},
        400: {"description": "Medication does not belong to this patient"},
    },
    dependencies=[Depends(require_patient_access)],
)
def create_adherence_log(
    patient_id: uuid.UUID, data: AdherenceLogCreate, db: DbSession
) -> AdherenceLogResponse:
    """Record a scheduled medication dose (taken or not taken)."""
    return AdherenceService(db).create_log(patient_id, data)


@router.get(
    "/patients/{patient_id}/adherence",
    response_model=List[AdherenceLogResponse],
    summary="List adherence logs for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def list_adherence_logs(patient_id: uuid.UUID, db: DbSession) -> List[AdherenceLogResponse]:
    return AdherenceService(db).list_logs(patient_id)


@router.patch(
    "/adherence/{adherence_id}",
    response_model=AdherenceLogResponse,
    summary="Update an adherence log (mark as taken)",
    responses={404: {"description": "Adherence log not found"}},
    dependencies=[Depends(require_patient_access)],
)
def update_adherence_log(
    adherence_id: uuid.UUID, data: AdherenceLogUpdate, db: DbSession
) -> AdherenceLogResponse:
    """Update taken status and/or taken_time for an adherence log entry."""
    return AdherenceService(db).update_log(adherence_id, data)
