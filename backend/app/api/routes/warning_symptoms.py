"""
CareLoop AI — Warning Symptom Routes
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, Response, status

from app.api.auth_deps import require_patient_access
from app.api.deps import DbSession
from app.schemas.warning_symptom import (
    WarningSymptomCreate,
    WarningSymptomResponse,
    WarningSymptomUpdate,
)
from app.services.warning_symptom import WarningSymptomService

router = APIRouter(tags=["Warning Symptoms"])


@router.post(
    "/patients/{patient_id}/warning-symptoms",
    response_model=WarningSymptomResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a warning symptom for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def create_symptom(
    patient_id: uuid.UUID, data: WarningSymptomCreate, db: DbSession
) -> WarningSymptomResponse:
    """
    Record a documented warning symptom for a patient.

    This endpoint stores data only.  No medical interpretation is performed.
    """
    return WarningSymptomService(db).create_symptom(patient_id, data)


@router.get(
    "/patients/{patient_id}/warning-symptoms",
    response_model=List[WarningSymptomResponse],
    summary="List warning symptoms for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def list_symptoms(patient_id: uuid.UUID, db: DbSession) -> List[WarningSymptomResponse]:
    return WarningSymptomService(db).list_symptoms(patient_id)


@router.patch(
    "/warning-symptoms/{symptom_id}",
    response_model=WarningSymptomResponse,
    summary="Update a warning symptom",
    responses={404: {"description": "Warning symptom not found"}},
    dependencies=[Depends(require_patient_access)],
)
def update_symptom(
    symptom_id: uuid.UUID, data: WarningSymptomUpdate, db: DbSession
) -> WarningSymptomResponse:
    return WarningSymptomService(db).update_symptom(symptom_id, data)


@router.delete(
    "/warning-symptoms/{symptom_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a warning symptom",
    responses={404: {"description": "Warning symptom not found"}},
    dependencies=[Depends(require_patient_access)],
)
def delete_symptom(symptom_id: uuid.UUID, db: DbSession) -> Response:
    WarningSymptomService(db).delete_symptom(symptom_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
