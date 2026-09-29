"""
CareLoop AI — Authentication Service (Phase 8A)

Registration and login for the patient self-service surface.

WHY REGISTER IS A SERVICE, NOT JUST A ROUTE
-------------------------------------------
Three tables change together - `app_users`, `patients`, and a `patient_access`
grant - and they must commit together or not at all.  The service keeps that
transaction in one place, and it uses `AccessControlService.grant_access` for
the grant rather than a direct insert, so the "one active grant" invariant and
the `revoked_at` history semantics are enforced by the same code the operator
CLI uses.

The register flow grants EXACTLY ONE `self` grant: to the patient record the
call just created.  The patient id is minted here, never taken from the caller,
so there is no input through which a caller could point the grant at somebody
else.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fastapi import HTTPException, status

from app.core.exceptions import EmailAlreadyRegisteredError
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.models.patient import Patient
from app.models.user import AccessRelationship, AppUser
from app.services.access_control import AccessControlService

logger = get_logger(__name__)


class AuthService:
    """
    Account creation and credential validation.

    Constructed per request with that request's session, matching every other
    service in the project.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def register(
        self, *, full_name: str, email: str, password: str
    ) -> tuple[AppUser, Patient]:
        """
        Create an account plus its own patient record and `self` grant.

        Returns the created `(user, patient)` pair.  The caller mints the token
        and builds the response; this method only persists.

        Raises `EmailAlreadyRegisteredError` (409) when the email is taken,
        checked both before and after the insert so a concurrent registration
        race surfaces as the same clean 409 rather than an IntegrityError.
        """
        email = email.strip().lower()

        if self._find_by_email(email) is not None:
            logger.info("auth_register_denied reason=email_taken")
            raise EmailAlreadyRegisteredError()

        try:
            password_hash = hash_password(password)
        except ValueError as exc:
            # Unreachable when the schema validated the password (it enforces
            # the same policy), but a direct service caller must not get a 500.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The password does not meet the required policy.",
            ) from exc

        user = AppUser(
            full_name=full_name,
            email=email,
            password_hash=password_hash,
            is_active=True,
            system_access=False,
        )
        patient = Patient(name=full_name)
        self._db.add(user)
        self._db.add(patient)
        try:
            # Flush assigns both ids so the grant below can reference them, and
            # surfaces a duplicate-email race as IntegrityError.
            self._db.flush()
        except IntegrityError as exc:
            self._db.rollback()
            logger.info("auth_register_denied reason=email_taken_race")
            raise EmailAlreadyRegisteredError() from exc

        # Exactly one `self` grant, to the patient just created.  The id was
        # minted above, so no caller-controlled value can redirect the grant.
        AccessControlService(self._db).grant_access(
            user, patient.id, AccessRelationship.SELF
        )
        logger.info("auth_registered user_id=%s patient_id=%s", user.id, patient.id)
        return user, patient

    def authenticate(self, *, email: str, password: str) -> AppUser | None:
        """
        Validate a login, returning the user or None.

        None covers every failure with the SAME outcome: unknown email, wrong
        password, and a deactivated account.  The caller turns it into one
        fixed 401, so the endpoint never reveals whether an email is registered
        or whether a shorter attack would be worthwhile.
        """
        user = self._find_by_email(email.strip().lower())
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def _find_by_email(self, email: str) -> AppUser | None:
        return self._db.execute(
            select(AppUser).where(AppUser.email == email)
        ).scalars().first()