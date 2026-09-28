"""
CareLoop AI — Patient Routes

The patient router is the only place a `patient_access` grant is ever created
through HTTP, and it creates exactly one: the caller's `self` grant to the
patient they just created.  That is not a shortcut for convenience, it is the
only way a principal could obtain access without an operator, and a principal
who can create a patient can already see that patient - the data is theirs, it
was just typed in by them.

Every other grant requires `app/cli/manage_access.py`, because a grant is a
delegation of authority over someone else's record and cannot be self-issued.
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Response, status

from app.api.auth_deps import AuthenticatedUserDep, PatientAccessDep
from app.api.deps import DbSession
from app.models.user import AccessRelationship
from app.schemas.patient import PatientCreate, PatientResponse, PatientUpdate
from app.services.access_control import AccessControlService
from app.services.patient import PatientService

router = APIRouter(prefix="/patients", tags=["Patients"])


@router.post(
    "",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a patient",
)
def create_patient(
    data: PatientCreate,
    db: DbSession,
    user: AuthenticatedUserDep,
) -> PatientResponse:
    """
    Register a new patient in the system.

    The caller is granted `self` access to the patient they just created.
    Without that, `POST /patients` would produce a record its own creator
    cannot read, which is a strange state to design for deliberately.
    """
    patient = PatientService(db).create_patient(data)
    AccessControlService(db).grant_access(
        user, patient.id, AccessRelationship.SELF
    )
    return patient


@router.get(
    "",
    response_model=List[PatientResponse],
    summary="List patients the caller can access",
)
def list_patients(
    db: DbSession,
    user: AuthenticatedUserDep,
    skip: int = 0,
    limit: int = 100,
) -> List[PatientResponse]:
    """
    Return a paginated list of patients the caller holds a grant for.

    Scoped, NOT filtered after the fact: the service is given the set of
    permitted ids and applies `skip`/`limit` to that set.  Filtering a
    full-table listing in Python would return short pages and a total count
    that describes the whole table, so the response would leak the size of the
    practice through pagination alone.
    """
    accessible = AccessControlService(db).accessible_patient_ids(user)
    return PatientService(db).list_patients(
        skip=skip, limit=limit, patient_ids=accessible
    )


@router.get(
    "/{patient_id}",
    response_model=PatientResponse,
    summary="Get a patient",
    responses={
        403: {"description": "Caller holds no grant for this patient"},
        404: {"description": "Patient not found"},
    },
)
def get_patient(
    patient_id: uuid.UUID,
    db: DbSession,
    _authorized: PatientAccessDep,
) -> PatientResponse:
    """Return a single patient by UUID."""
    return PatientService(db).get_patient(patient_id)


@router.patch(
    "/{patient_id}",
    response_model=PatientResponse,
    summary="Update a patient",
    responses={
        403: {"description": "Caller holds no grant for this patient"},
        404: {"description": "Patient not found"},
    },
)
def update_patient(
    patient_id: uuid.UUID,
    data: PatientUpdate,
    db: DbSession,
    _authorized: PatientAccessDep,
) -> PatientResponse:
    """Partially update a patient record."""
    return PatientService(db).update_patient(patient_id, data)


@router.delete(
    "/{patient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a patient",
    responses={
        403: {"description": "Caller holds no grant for this patient"},
        404: {"description": "Patient not found"},
        409: {"description": "Patient has existing records — delete those first"},
    },
)
def delete_patient(
    patient_id: uuid.UUID,
    db: DbSession,
    _authorized: PatientAccessDep,
) -> Response:
    """
    Delete a patient record.

    Will return 409 Conflict if the patient has medications, appointments,
    or other records.  Delete those records first.

    Any `patient_access` grants to this patient are removed by the FK's
    ON DELETE CASCADE, so deleting a record also revokes every caregiver's and
    care team's access to it in the same statement - no window where a grant
    outlives the patient it referred to.
    """
    PatientService(db).delete_patient(patient_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
