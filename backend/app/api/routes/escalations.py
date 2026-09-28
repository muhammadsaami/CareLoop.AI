"""
CareLoop AI — Escalation Routes (Phase 6)

An escalation is the system's record that a CONFIGURED rule matched a check-in,
plus the human workflow that followed.  These routes are the read and
lifecycle side of that record.

WHAT THESE ROUTES DELIBERATELY DO NOT OFFER
  * No route that sets an escalation's status directly.  Every state change
    goes through `acknowledge` / `resolve` / `cancel`, each of which enforces
    the allowed transitions.  A `PATCH /escalations/{id}` that accepted an
    arbitrary status would let a caller mark an escalation `notified` when no
    alert was ever sent, which is exactly the false record the status column
    exists to prevent.
  * No route that creates an escalation.  Only the rule layer can, from stored
    facts.  A route that accepted a rule code from a client would make the
    whole safety argument optional.
  * No route that triggers a caregiver notification directly.  Notification is
    requested as part of the submission that raised the escalation, and the
    Celery task covers the rest; an operator-triggered send would need its own
    audit record to explain a message nobody asked for.

EVERY READ IS PATIENT-SCOPED
Escalation ids are reachable only through `/patients/{patient_id}/escalations`.
A 404 also covers "belongs to someone else", so this cannot be used to probe
for another patient's escalation ids.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from app.api.auth_deps import require_patient_access
from app.api.deps import DbSession, EscalationServiceDep
from app.core.exceptions import EscalationNotFoundError
from app.models.escalation import EscalationStatus
from app.schemas.checkin import (
    EscalationResponse,
    EscalationTransitionRequest,
)
from app.services.patient import PatientService

router = APIRouter(
    prefix="/patients",
    tags=["escalations"],
)


def _to_response(escalation) -> EscalationResponse:
    return EscalationResponse.model_validate(escalation)


def _get_owned(
    patient_id: uuid.UUID,
    escalation_id: uuid.UUID,
    db: DbSession,
    service: EscalationServiceDep,
):
    """
    Load the escalation, proving it belongs to the path's patient.

    One helper for all five routes, because this check is the authorization
    boundary and duplicating it five times is five chances to get one of them
    wrong.  Raises 404 - not 403 - for another patient's escalation: a 403
    confirms the id exists, which turns the endpoint into an oracle for
    discovering other patients' escalation ids.
    """
    PatientService(db).get_patient(patient_id)
    escalation = service.get(escalation_id)
    if escalation.patient_id != patient_id:
        raise EscalationNotFoundError()
    return escalation


@router.get(
    "/{patient_id}/escalations",
    response_model=list[EscalationResponse],
    summary="List a patient's escalations, newest first",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def list_escalations(
    patient_id: uuid.UUID,
    db: DbSession,
    service: EscalationServiceDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status_filter: Optional[EscalationStatus] = Query(
        None,
        alias="status",
        description="Filter by lifecycle status, e.g. pending or notified.",
    ),
) -> list[EscalationResponse]:
    """
    The patient's escalation history.

    Useful as the "what is outstanding?" view: filter by `status=pending` for
    escalations a human has not yet seen, or `status=resolved` for the audit
    view of what was followed up.

    Does not include a total count in the envelope - a client that needs one
    can page until a short page is returned.  Kept simple deliberately; the
    check-in history route is the one that needed a total.
    """
    PatientService(db).get_patient(patient_id)
    escalations = service.list_for_patient(
        patient_id, skip=skip, limit=limit, status=status_filter
    )
    return [_to_response(escalation) for escalation in escalations]


@router.get(
    "/{patient_id}/escalations/{escalation_id}",
    response_model=EscalationResponse,
    summary="Get one escalation by ID",
    responses={404: {"description": "Escalation not found for this patient"}},
    dependencies=[Depends(require_patient_access)],
)
def get_escalation(
    patient_id: uuid.UUID,
    escalation_id: uuid.UUID,
    db: DbSession,
    service: EscalationServiceDep,
) -> EscalationResponse:
    """
    One escalation, including its notification delivery state.

    `notification_blocked_reason` is the field to look at when an escalation
    shows `status=pending` and nobody has heard about it: a code there means the
    system decided not to send, and says why.  The common cause is a patient
    record with no caregiver contact.
    """
    escalation = _get_owned(patient_id, escalation_id, db, service)
    return _to_response(escalation)


@router.post(
    "/{patient_id}/escalations/{escalation_id}/acknowledge",
    response_model=EscalationResponse,
    summary="Acknowledge an escalation",
    responses={
        404: {"description": "Escalation not found for this patient"},
        409: {"description": "This escalation cannot be acknowledged now"},
    },
    dependencies=[Depends(require_patient_access)],
)
def acknowledge_escalation(
    patient_id: uuid.UUID,
    escalation_id: uuid.UUID,
    db: DbSession,
    service: EscalationServiceDep,
) -> EscalationResponse:
    """
    Record that a human has seen the escalation.

    Only permitted from `notified`.  Acknowledging a `pending` escalation would
    assert that a caregiver saw an alert that was never sent.
    """
    _get_owned(patient_id, escalation_id, db, service)
    updated = service.acknowledge(escalation_id)
    db.commit()
    return _to_response(updated)


@router.post(
    "/{patient_id}/escalations/{escalation_id}/resolve",
    response_model=EscalationResponse,
    summary="Resolve an escalation",
    responses={
        404: {"description": "Escalation not found for this patient"},
        409: {"description": "This escalation cannot be resolved now"},
    },
    dependencies=[Depends(require_patient_access)],
)
def resolve_escalation(
    patient_id: uuid.UUID,
    escalation_id: uuid.UUID,
    payload: EscalationTransitionRequest,
    db: DbSession,
    service: EscalationServiceDep,
) -> EscalationResponse:
    """
    Close the loop on an escalation, optionally with a short note.

    The note is operational prose written by a human ("called the patient, no
    action needed").  It is stored on the escalation row only - never rendered
    into a notification body and never logged.
    """
    _get_owned(patient_id, escalation_id, db, service)
    updated = service.resolve(escalation_id, note=payload.note)
    db.commit()
    return _to_response(updated)


@router.post(
    "/{patient_id}/escalations/{escalation_id}/cancel",
    response_model=EscalationResponse,
    summary="Cancel an escalation that should not have been raised",
    responses={
        404: {"description": "Escalation not found for this patient"},
        409: {"description": "This escalation cannot be cancelled now"},
    },
    dependencies=[Depends(require_patient_access)],
)
def cancel_escalation(
    patient_id: uuid.UUID,
    escalation_id: uuid.UUID,
    db: DbSession,
    service: EscalationServiceDep,
) -> EscalationResponse:
    """
    Withdraw an escalation.

    Distinct from `resolve` on purpose: resolving says the loop was followed and
    finished, cancelling says the flag itself was not actionable.  A human still
    chooses, so the audit trail cannot quietly turn "false alarm" into
    "handled".
    """
    _get_owned(patient_id, escalation_id, db, service)
    updated = service.cancel(escalation_id)
    db.commit()
    return _to_response(updated)
