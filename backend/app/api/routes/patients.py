"""
CareLoop AI — Patient Routes
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Response, status

from app.api.deps import DbSession
from app.schemas.patient import PatientCreate, PatientResponse, PatientUpdate
from app.services.patient import PatientService

router = APIRouter(prefix="/patients", tags=["Patients"])


@router.post(
    "",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a patient",
)
def create_patient(data: PatientCreate, db: DbSession) -> PatientResponse:
    """Register a new patient in the system."""
    return PatientService(db).create_patient(data)


@router.get(
    "",
    response_model=List[PatientResponse],
    summary="List all patients",
)
def list_patients(db: DbSession, skip: int = 0, limit: int = 100) -> List[PatientResponse]:
    """Return a paginated list of all patients."""
    return PatientService(db).list_patients(skip=skip, limit=limit)


@router.get(
    "/{patient_id}",
    response_model=PatientResponse,
    summary="Get a patient",
    responses={404: {"description": "Patient not found"}},
)
def get_patient(patient_id: uuid.UUID, db: DbSession) -> PatientResponse:
    """Return a single patient by UUID."""
    return PatientService(db).get_patient(patient_id)


@router.patch(
    "/{patient_id}",
    response_model=PatientResponse,
    summary="Update a patient",
    responses={404: {"description": "Patient not found"}},
)
def update_patient(
    patient_id: uuid.UUID, data: PatientUpdate, db: DbSession
) -> PatientResponse:
    """Partially update a patient record."""
    return PatientService(db).update_patient(patient_id, data)


@router.delete(
    "/{patient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a patient",
    responses={
        404: {"description": "Patient not found"},
        409: {"description": "Patient has existing records — delete those first"},
    },
)
def delete_patient(patient_id: uuid.UUID, db: DbSession) -> Response:
    """
    Delete a patient record.

    Will return 409 Conflict if the patient has medications, appointments,
    or other records.  Delete those records first.
    """
    PatientService(db).delete_patient(patient_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
