"""
CareLoop AI — Medication Routes
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, Response, status

from app.api.auth_deps import require_patient_access
from app.api.deps import DbSession
from app.schemas.medication import MedicationCreate, MedicationResponse, MedicationUpdate
from app.services.medication import MedicationService

router = APIRouter(tags=["Medications"])


# ── Nested under patient ───────────────────────────────────────────────────────

@router.post(
    "/patients/{patient_id}/medications",
    response_model=MedicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a medication for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def create_medication(
    patient_id: uuid.UUID, data: MedicationCreate, db: DbSession
) -> MedicationResponse:
    """Create a new medication record for the given patient."""
    return MedicationService(db).create_medication(patient_id, data)


@router.get(
    "/patients/{patient_id}/medications",
    response_model=List[MedicationResponse],
    summary="List medications for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def list_medications(patient_id: uuid.UUID, db: DbSession) -> List[MedicationResponse]:
    """Return all medications for the given patient."""
    return MedicationService(db).list_medications(patient_id)


# ── Standalone by medication ID ────────────────────────────────────────────────

@router.get(
    "/medications/{medication_id}",
    response_model=MedicationResponse,
    summary="Get a medication",
    responses={404: {"description": "Medication not found"}},
    dependencies=[Depends(require_patient_access)],
)
def get_medication(medication_id: uuid.UUID, db: DbSession) -> MedicationResponse:
    """Return a single medication by its UUID."""
    return MedicationService(db).get_medication(medication_id)


@router.patch(
    "/medications/{medication_id}",
    response_model=MedicationResponse,
    summary="Update a medication",
    responses={404: {"description": "Medication not found"}},
    dependencies=[Depends(require_patient_access)],
)
def update_medication(
    medication_id: uuid.UUID, data: MedicationUpdate, db: DbSession
) -> MedicationResponse:
    """Partially update a medication record."""
    return MedicationService(db).update_medication(medication_id, data)


@router.delete(
    "/medications/{medication_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a medication",
    responses={404: {"description": "Medication not found"}},
    dependencies=[Depends(require_patient_access)],
)
def delete_medication(medication_id: uuid.UUID, db: DbSession) -> Response:
    """Delete a medication record."""
    MedicationService(db).delete_medication(medication_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
