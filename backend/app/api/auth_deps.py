"""
CareLoop AI — Authentication & Authorization Dependencies

This module is the enforcement point.  Two layers:

  * `require_authenticated_user` - installed once per router in `app.main`.
    Every route on a protected router needs a valid bearer token.  One line per
    router means a new router is unprotected only if someone deliberately
    chooses not to add it, and a test asserts the coverage.

  * `require_patient_access` - resolves WHICH patient the request is about and
    asks `AccessControlService` whether the caller may touch them.  It handles
    three shapes, because the codebase uses all three:
      1. `/patients/{patient_id}/...`          - the id is in the path
      2. `/medications/{medication_id}`        - a resource whose owner must be
                                                resolved from its own row
      3. `patient_id` in a JSON body or form   - three routes do this, and they
                                                pass the parsed value in

WHY AUTHORIZATION IS A ROUTE DEPENDENCY, NOT A MIDDLEWARE
---------------------------------------------------------
A middleware would have to parse the URL to learn which patient a request is
about, re-implementing routing logic in a layer that cannot see the route
table.  A dependency sees the resolved route and its `path_params`, so an
unrecognized path shape is detectable rather than silently allowed.

TWO LAYERS, BOTH REQUIRED
-------------------------
`require_authenticated_user` is attached ONCE PER ROUTER, so authentication
cannot be forgotten on a route.  Authorization cannot be done the same way,
because this API names its patient three different ways (URL path, resource id,
request body), so it is attached per route.  Per-route attachment is exactly
the thing that gets forgotten, so the guarantee comes from a test instead:
`tests/test_security_routes.py` walks the real route table and fails if any
route on a protected router is missing an authorization dependency, or names a
`*_id` path parameter the registry here does not cover.  A new route cannot
join the surface unauthenticated without a test going red.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any, Optional

from fastapi import Depends, Form, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.security import InvalidTokenError, decode_access_token
from app.models.user import AppUser
from app.schemas.agent import GroundedAnswerRequest
from app.schemas.rag import RagIndexRequest, RagRetrieveRequest
from app.services.access_control import AccessControlService

logger = get_logger(__name__)

# `auto_error=False` so this module raises the project's own
# `AuthenticationError` (which the global handler renders as a consistent 401
# with a `WWW-Authenticate` header) instead of FastAPI's default 403, which is
# the wrong status for a missing credential and carries no header.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[Session, Depends(get_db)]


def get_access_control_service(db: DbSession) -> AccessControlService:
    """Request-scoped access control service."""
    return AccessControlService(db)


AccessControlServiceDep = Annotated[
    AccessControlService, Depends(get_access_control_service)
]


# ═══════════════════════════════════════════════════════════════════════════
# Resource ownership registry
# ═══════════════════════════════════════════════════════════════════════════
#
# Maps a URL path parameter to the model that owns it, so a request keyed by a
# resource id can be traced back to its patient.  Each model must expose
# `patient_id`.
#
# A row whose owner cannot be resolved is treated as absent (404), never as
# authorized.  Defaulting to "allow" on an unknown shape would be the worst
# possible failure mode for a new route added under time pressure.

RESOURCE_OWNERS: dict[str, Any] = {}


def _register_resource_owners() -> None:
    """
    Populate `RESOURCE_OWNERS` from the models.

    Imported lazily and called once: the model modules import `Base` from
    `app.core.database`, and this module is imported by route modules that
    themselves sit above that layer, so a module-scope import here would
    create a cycle.
    """
    from app.models.adherence_log import AdherenceLog
    from app.models.appointment import Appointment
    from app.models.checkin import CheckIn
    from app.models.discharge_document import DischargeDocument
    from app.models.escalation import Escalation
    from app.models.medication import Medication
    from app.models.notification import Notification
    from app.models.reminder import Reminder
    from app.models.warning_symptom import WarningSymptom

    RESOURCE_OWNERS.update(
        {
            "adherence_id": AdherenceLog,
            "appointment_id": Appointment,
            "checkin_id": CheckIn,
            "document_id": DischargeDocument,
            "escalation_id": Escalation,
            "medication_id": Medication,
            "notification_id": Notification,
            "reminder_id": Reminder,
            "symptom_id": WarningSymptom,
        }
    )


_register_resource_owners()

#: Path parameters that name a patient directly rather than a resource.
PATIENT_PATH_PARAM = "patient_id"


# ═══════════════════════════════════════════════════════════════════════════
# Authentication
# ═══════════════════════════════════════════════════════════════════════════


def get_current_user(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(_bearer_scheme)
    ],
    db: DbSession,
) -> AppUser:
    """
    Resolve the bearer token to a live `AppUser`, or raise 401.

    The user row is re-read on every request rather than trusted from the
    token's claims.  That costs one indexed lookup and buys the property that
    deactivating an account or revoking a grant takes effect IMMEDIATELY,
    instead of whenever the outstanding token happens to expire - which for a
    stolen credential is up to `ACCESS_TOKEN_EXPIRE_MINUTES` of continued
    access after the incident that prompted the deactivation.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError(internal_detail="reason=no_bearer_credential")

    if (credentials.scheme or "").lower() != "bearer":
        raise AuthenticationError(
            internal_detail=f"reason=unsupported_scheme:{credentials.scheme!r}"
        )

    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        # `exc.reason` is logged, never returned.  See AuthenticationError.
        raise AuthenticationError(internal_detail=f"reason={exc.reason}") from exc

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        # decode_access_token already validates this, so reaching here is a bug
        # rather than bad input - fail closed rather than trusting it.
        raise AuthenticationError(
            internal_detail="reason=unparseable_sub_claim"
        ) from exc

    user = db.get(AppUser, user_id)
    if user is None:
        raise AuthenticationError(
            internal_detail=f"reason=subject_not_found:user_id={user_id}"
        )

    if not user.is_active:
        # A valid, correctly-signed token for a deactivated account. 401, not
        # 403: re-authenticating cannot help, but the caller should stop
        # presenting this credential, and 403 would suggest a different
        # credential would work.
        raise AuthenticationError(
            internal_detail=f"reason=user_inactive:user_id={user_id}"
        )

    return user


CurrentUserDep = Annotated[AppUser, Depends(get_current_user)]


def require_authenticated_user(user: CurrentUserDep) -> AppUser:
    """
    Router-level gate.

    Registered as a router dependency in `app.main` so every route on the
    router requires a valid token.  Returns the user so it can be used directly
    as a route parameter where a handler needs the identity.
    """
    return user


AuthenticatedUserDep = Annotated[AppUser, Depends(require_authenticated_user)]


def require_system_access(
    user: AuthenticatedUserDep,
    access: AccessControlServiceDep,
) -> AppUser:
    """
    Gate the two endpoints that act on every patient at once.

    `POST /notifications/dispatch` and `POST /notifications/retry` walk the
    whole notification table, so a per-patient grant cannot describe permission
    to call them.
    """
    access.require_system_access(user)
    return user


SystemOperatorDep = Annotated[AppUser, Depends(require_system_access)]


# ═══════════════════════════════════════════════════════════════════════════
# Authorization — patient named directly in the path
# ═══════════════════════════════════════════════════════════════════════════


def require_patient_access(
    request: Request,
    user: AuthenticatedUserDep,
    db: DbSession,
    access: AccessControlServiceDep,
) -> AppUser:
    """
    Authorize the patient this request is about, whichever way it names them.

    Resolution order, and why:

    1. `{patient_id}` in the path - the common case, and unambiguous.
    2. A `{*_id}` resource path parameter - resolve the row, then its owner.
    3. Neither - a route that reaches a patient some other way (a JSON body
       or a form field) declares that explicitly with one of the
       `require_*_patient_access` functions below and does NOT use this
       dependency.

    Case 3 is the reason this function fails LOUDLY rather than passing when it
    cannot identify a patient: a route that reaches patient data without any of
    the recognized shapes would otherwise be authorized by default, which is
    exactly the bug this whole module exists to prevent.  It raises 403 and logs
    the path, so the mistake is visible in a test and in production rather than
    silently permitting access.
    """
    params = request.path_params

    patient_id = params.get(PATIENT_PATH_PARAM)
    if patient_id is not None:
        access.require_patient_access(user, _uuid_param(patient_id, PATIENT_PATH_PARAM))
        return user

    for param_name, model in RESOURCE_OWNERS.items():
        raw = params.get(param_name)
        if raw is None:
            continue
        resource_id = _uuid_param(raw, param_name)
        owner_id = _resolve_owner_patient_id(db, model, resource_id)
        access.require_resource_access(
            user, patient_id=owner_id, resource_label=param_name
        )
        return user

    logger.error(
        "authorization_gap path=%s - no patient or resource path parameter "
        "recognized; refusing",
        request.url.path,
    )
    # 403 rather than 404: the caller did authenticate, and the failure here is
    # a server-side wiring gap rather than a missing record.  The log line is
    # the point - it names the route that needs a shape this module knows about.
    raise HTTPException(
        status_code=403,
        detail=(
            "This endpoint has no recognized patient or resource path "
            "parameter, so access cannot be established. See server logs."
        ),
    )


def _uuid_param(raw: Any, param_name: str) -> uuid.UUID:
    """
    Parse a path parameter as a UUID, answering 422 if it is malformed.

    This dependency runs BEFORE the route handler's own parameter validation, so
    `/discharge-documents/not-a-uuid` reaches this code as the raw string.
    Without the check, `uuid.UUID(...)` would raise `ValueError` and become an
    unhandled 500 - a malformed id in a URL would be the only way to crash the
    endpoint. 422 is what FastAPI itself returns for this input, so answering it
    here keeps the response identical whether or not authorization is attached.
    """
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid UUID for path parameter '{param_name}'.",
        ) from exc


def _resolve_owner_patient_id(
    db: Session, model: Any, resource_id: uuid.UUID
) -> Optional[uuid.UUID]:
    """
    The `patient_id` owning `resource_id`, or None if the row does not exist.

    Returns None rather than raising so `require_resource_access` can answer
    "no such resource" and "not yours" with the same 404.  It selects only the
    ownership column - never the row's clinical content - so an unauthorized
    request does not pull a patient's data into the process at all.
    """
    statement = select(model.patient_id).where(model.id == resource_id)
    return db.execute(statement).scalars().first()


PatientAccessDep = Annotated[AppUser, Depends(require_patient_access)]


# ═══════════════════════════════════════════════════════════════════════════
# Authorization — patient named in a request body
# ═══════════════════════════════════════════════════════════════════════════
#
# Four routes reach a patient with no patient id in the path:
#
#   POST /agent/query                  (JSON body `patient_id`)
#   POST /rag/retrieve                 (JSON body `patient_id`)
#   POST /rag/documents/{id}/index     (JSON body `patient_id`, and a path id)
#   POST /discharge-documents          (multipart form field `patient_id`)
#
# Each dependency below declares the SAME pydantic model the handler declares.
# FastAPI parses the body once and hands the same object to both, so the access
# check reads the real value that is about to be used - it cannot disagree with
# the handler, and it cannot be satisfied by a value the handler ignores.
#
# The alternative - re-reading `await request.json()` - does not work for the
# multipart upload (the body stream is already consumed by the file parser) and
# would duplicate parsing for the other three.
#
# WHY SEPARATE FUNCTIONS INSTEAD OF ONE GENERIC ONE
# --------------------------------------------------
# FastAPI resolves a bare scalar dependency parameter as a QUERY parameter, so a
# single `def check(patient_id: uuid.UUID, ...)` would look for the patient in
# the query string and then read a different value out of the body - authorizing
# one patient while acting on another. Declaring the model in the signature is
# what ties the check to the value. Four near-identical functions are the price
# of that, and they are worth it.


def require_agent_query_patient_access(
    payload: GroundedAnswerRequest,
    user: AuthenticatedUserDep,
    access: AccessControlServiceDep,
) -> AppUser:
    """Access check for `POST /agent/query`."""
    access.require_patient_access(user, payload.patient_id)
    return user


def require_rag_retrieve_patient_access(
    payload: RagRetrieveRequest,
    user: AuthenticatedUserDep,
    access: AccessControlServiceDep,
) -> AppUser:
    """Access check for `POST /rag/retrieve`."""
    access.require_patient_access(user, payload.patient_id)
    return user


def require_rag_index_patient_access(
    payload: RagIndexRequest,
    user: AuthenticatedUserDep,
    access: AccessControlServiceDep,
) -> AppUser:
    """
    Access check for `POST /rag/documents/{document_id}/index`.

    This route carries TWO attacker-supplied identifiers - `patient_id` in the
    body and `document_id` in the path - and only the body patient is a grant
    the caller can be checked against here, because resolving a path document id
    to its owner means reading the document row first.

    The pair is reconciled one layer down. `RagIndexingService.index_document`
    calls `_load_owned_document(patient_id, document_id)` BEFORE any vector
    store call, so a mismatched pair 404s without touching the index. That is
    what stops a caller with a valid document id from writing that document's
    chunks into their own patient's collection, and it is why authorizing the
    body patient alone is sufficient here rather than merely convenient.

    Keeping the reconciliation in the service also means it holds for direct
    callers (the operator re-index path) that never traverse HTTP.
    """
    access.require_patient_access(user, payload.patient_id)
    return user


def require_discharge_upload_patient_access(
    # `Form(...)`, not `Query(...)`: the field arrives in the same multipart
    # body as the file, and `UploadFile` forces FastAPI to parse multipart.
    patient_id: Annotated[uuid.UUID, Form()],
    user: AuthenticatedUserDep,
    access: AccessControlServiceDep,
) -> AppUser:
    """
    Access check for `POST /discharge-documents`.

    Declares `patient_id` as a form field, which is also what makes FastAPI read
    the body as multipart - the same signal the file upload needs.  The route
    declares the identical field, and the two resolve to the same value.
    """
    access.require_patient_access(user, patient_id)
    return user


__all__ = [
    "AccessControlServiceDep",
    "AuthenticatedUserDep",
    "CurrentUserDep",
    "PATIENT_PATH_PARAM",
    "PatientAccessDep",
    "RESOURCE_OWNERS",
    "SystemOperatorDep",
    "get_access_control_service",
    "get_current_user",
    "require_agent_query_patient_access",
    "require_authenticated_user",
    "require_discharge_upload_patient_access",
    "require_patient_access",
    "require_rag_index_patient_access",
    "require_rag_retrieve_patient_access",
    "require_system_access",
]
