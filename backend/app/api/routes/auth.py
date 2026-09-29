"""
CareLoop AI — Authentication Routes (Phase 8A)

`/register` and `/login` are the patient self-service surface, and are PUBLIC by
design: an account must be creatable, and a credential must be exchangeable,
without already holding a credential.  Everything else this router serves
(`/me`) is protected by `require_authenticated_user` on the route itself, so the
router as a whole can stay off the PROTECTED list in `app.main` without opening
anything.

WHY THERE IS NO /LOGOUT
-----------------------
There is no token-revocation mechanism in the system (a JWT is validated, not
checked against a blocklist), so a logout endpoint would be either a no-op that
pretends to do something or a silent lie.  The access token expires in minutes;
a client discards it.  A real logout - invalidating outstanding tokens - is a
revocation feature and is deliberately out of scope.

WHY REGISTRATION NEVER TAKES A CONTACT NUMBER
---------------------------------------------
Registration supplies only `{full_name, email, password}` and creates a patient
without a phone number (`contact_number` is NULL).  A phone number can be added
through the normal `PATCH /patients` flow later; the account should not fail to
exist because an optional field was refused.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth_deps import CurrentUserDep, require_authenticated_user
from app.api.deps import DbSession
from app.core.security import create_access_token
from app.schemas.auth import (
    AuthSessionResponse,
    LoginRequest,
    RegisterRequest,
    UserResponse,
)
from app.services.access_control import AccessControlService
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _session_response(user, patient_id) -> AuthSessionResponse:
    """A signed session for `user`, with its `self` patient id when present."""
    return AuthSessionResponse(
        access_token=create_access_token(str(user.id)),
        token_type="bearer",
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            patient_id=patient_id,
        ),
        patient_id=patient_id,
    )


@router.post(
    "/register",
    response_model=AuthSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a patient account",
    responses={409: {"description": "An account with this email already exists"}},
)
def register(data: RegisterRequest, db: DbSession) -> AuthSessionResponse:
    """Create a patient account and return a signed session for it."""
    user, patient = AuthService(db).register(
        full_name=data.full_name, email=data.email, password=data.password
    )
    return _session_response(user, patient.id)


@router.post(
    "/login",
    response_model=AuthSessionResponse,
    summary="Exchange credentials for a session",
    responses={401: {"description": "Invalid email or password"}},
)
def login(data: LoginRequest, db: DbSession) -> AuthSessionResponse:
    """
    Exchange an email and password for a signed session.

    One 401 body for every failure - unknown email, wrong password, disabled
    account - so this endpoint cannot be used to learn whether an email exists.
    """
    user = AuthService(db).authenticate(email=data.email, password=data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    patient_ids = AccessControlService(db).self_patient_ids(user)
    patient_id = patient_ids[0] if patient_ids else None
    return _session_response(user, patient_id)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the authenticated account",
    dependencies=[Depends(require_authenticated_user)],
)
def me(user: CurrentUserDep, db: DbSession) -> UserResponse:
    """
    Return the account behind the presented bearer token.

    Protected by `require_authenticated_user` on this route, so it stays
    public-route-free even though the router is not under `PROTECTED`.
    """
    patient_ids = AccessControlService(db).self_patient_ids(user)
    patient_id = patient_ids[0] if patient_ids else None
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        patient_id=patient_id,
    )