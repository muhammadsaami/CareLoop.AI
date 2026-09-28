"""
CareLoop AI — Appointment Routes
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, Response, status

from app.api.auth_deps import require_patient_access
from app.api.deps import DbSession
from app.schemas.appointment import AppointmentCreate, AppointmentResponse, AppointmentUpdate
from app.services.appointment import AppointmentService

router = APIRouter(tags=["Appointments"])


@router.post(
    "/patients/{patient_id}/appointments",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an appointment for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def create_appointment(
    patient_id: uuid.UUID, data: AppointmentCreate, db: DbSession
) -> AppointmentResponse:
    return AppointmentService(db).create_appointment(patient_id, data)


@router.get(
    "/patients/{patient_id}/appointments",
    response_model=List[AppointmentResponse],
    summary="List appointments for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def list_appointments(patient_id: uuid.UUID, db: DbSession) -> List[AppointmentResponse]:
    return AppointmentService(db).list_appointments(patient_id)


@router.patch(
    "/appointments/{appointment_id}",
    response_model=AppointmentResponse,
    summary="Update an appointment",
    responses={404: {"description": "Appointment not found"}},
    dependencies=[Depends(require_patient_access)],
)
def update_appointment(
    appointment_id: uuid.UUID, data: AppointmentUpdate, db: DbSession
) -> AppointmentResponse:
    return AppointmentService(db).update_appointment(appointment_id, data)


@router.delete(
    "/appointments/{appointment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete an appointment",
    responses={404: {"description": "Appointment not found"}},
    dependencies=[Depends(require_patient_access)],
)
def delete_appointment(appointment_id: uuid.UUID, db: DbSession) -> Response:
    AppointmentService(db).delete_appointment(appointment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
