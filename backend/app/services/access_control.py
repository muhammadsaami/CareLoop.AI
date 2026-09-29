"""
CareLoop AI â€” Access Control

The single place that answers "may this principal act on this patient?".

Every patient-scoped endpoint reaches this service, through one of two paths in
`app.api.deps`: a `patient_id` from the URL, or a resource row resolved back to
its owning patient.  Keeping both paths in one file is deliberate - two
independent authorization implementations are how a system ends up with one
route that was quietly never protected.

WHAT "AUTHORIZED" MEANS HERE
----------------------------
An active `patient_access` row exists for (user, patient).  That is the whole
rule.  The relationship on the grant (`self`, `caregiver`, `care_team`) is
stored for audit but does not currently narrow what the holder may do; see
`app.models.user` for why, and the README for the deployment implications.

WHY THE 403 / 404 SPLIT
-----------------------
The two failure modes are not interchangeable, and choosing wrongly either
leaks existence or breaks a legitimate workflow:

* Patient named in the URL, caller has no grant -> 403.  The caller already
  holds the id (they put it in the request), so "not authorized" reveals
  nothing about whether the patient exists.  A 404 here would be actively
  confusing: "no such patient" and "not your patient" are different situations
  and the caller can act on the difference.

* Caller names a RESOURCE id (`/medications/{id}`) and has no grant to its
  owner -> 404.  To answer at all, the resource row had to be loaded to learn
  which patient owns it.  Answering 403 would confirm the resource exists,
  turning the endpoint into an existence oracle.  404 also matches what this
  codebase already returns for a cross-patient resource lookup, so the two
  paths agree.

The grant lookup never joins `patients`, so a nonexistent patient id and an
unauthorized one are indistinguishable to the caller.  That is the property
that makes 403 safe above.
"""
from __future__ import annotations

import uuid
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.core.logging import get_logger
from app.models.user import AccessRelationship, AppUser, PatientAccess

logger = get_logger(__name__)


class AccessControlService:
    """
    Authorization decisions over `app_users` and `patient_access`.

    Constructed per request with that request's session, matching every other
    service in the project.  It holds no state and caches nothing: a cached
    authorization decision is a revoked grant that still works.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # â”€â”€ Grant queries â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def has_access(self, user: AppUser, patient_id: uuid.UUID) -> bool:
        """True when `user` holds an active grant to `patient_id`."""
        return self._active_grant(user.id, patient_id) is not None

    def accessible_patient_ids(
        self, user: AppUser, candidate_ids: Optional[Iterable[uuid.UUID]] = None
    ) -> set[uuid.UUID]:
        """
        The subset of `candidate_ids` this user may access.

        `candidate_ids=None` means "every patient this user holds any grant to",
        which is what the patient list endpoint needs.
        """
        statement = select(PatientAccess.patient_id).where(
            PatientAccess.user_id == user.id,
            PatientAccess.revoked_at.is_(None),
        )
        if candidate_ids is not None:
            candidates = list(candidate_ids)
            if not candidates:
                return set()
            statement = statement.where(PatientAccess.patient_id.in_(candidates))
        return set(self._db.execute(statement).scalars().all())

    def self_patient_ids(self, user: AppUser) -> list[uuid.UUID]:
        """
        The patient ids this user holds an ACTIVE `self` grant to.

        This is the account's own identity, not its reach: a caregiver with
        access to six patients and a `self` grant to none returns an empty
        list, which is exactly right for `/auth/me`.  A self-registered patient
        holds exactly one such grant, minted at registration; operators and
        caregivers never hold `self`.  Ordered by grant time so the answer is
        deterministic even in the theoretical multi-self-grant case.
        """
        statement = (
            select(PatientAccess.patient_id)
            .where(
                PatientAccess.user_id == user.id,
                PatientAccess.relationship == AccessRelationship.SELF,
                PatientAccess.revoked_at.is_(None),
            )
            .order_by(PatientAccess.granted_at)
        )
        return list(self._db.execute(statement).scalars().all())

    # â”€â”€ Decisions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def require_patient_access(self, user: AppUser, patient_id: uuid.UUID) -> None:
        """
        Authorize a request that names its patient directly. 403 if refused.

        Never confirms or denies that the patient exists: the grant lookup does
        not touch the `patients` table at all.
        """
        if self._active_grant(user.id, patient_id) is not None:
            return
        logger.info(
            "access_denied reason=no_grant user_id=%s patient_id=%s",
            user.id,
            patient_id,
        )
        raise AuthorizationError()

    def require_resource_access(
        self,
        user: AppUser,
        *,
        patient_id: Optional[uuid.UUID],
        resource_label: str,
    ) -> None:
        """
        Authorize a request keyed by a resource id. 404 if refused.

        `patient_id` is the resource's owner, resolved by the caller from the
        row it loaded.  When the row does not exist, `patient_id` is None and
        the same 404 comes back - the caller cannot distinguish "no such
        resource" from "not yours", which is the point.
        """
        if patient_id is not None and self.has_access(user, patient_id):
            return
        logger.info(
            "access_denied reason=no_grant_on_resource user_id=%s resource=%s",
            user.id,
            resource_label,
        )
        raise ResourceNotFoundError()

    def require_system_access(self, user: AppUser) -> None:
        """
        Authorize an action spanning every patient. 403 if refused.

        Used only by the two whole-system notification endpoints.  Per-patient
        grants cannot express "may process the entire queue", so this is an
        explicit separately-stored privilege rather than something inferred.
        """
        if user.system_access:
            return
        logger.info("access_denied reason=no_system_access user_id=%s", user.id)
        raise AuthorizationError(
            "This action requires operator (system) access."
        )

    # â”€â”€ Grant management (operator only) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    #
    # No HTTP route calls any of this.  A caller able to reach it could mint
    # themselves a `care_team` grant and then read every patient in the system,
    # so grant management lives behind the CLI (`app/cli/manage_access.py`).
    # Nothing in the HTTP surface can widen or narrow a grant, and that is the
    # single most important property of this module.

    def grant_access(
        self,
        user: AppUser,
        patient_id: uuid.UUID,
        relationship: str = AccessRelationship.SELF,
    ) -> PatientAccess:
        """
        Grant `user` access to `patient_id`.

        Idempotent with respect to an ACTIVE grant: re-granting returns the
        existing row instead of adding a second one, so running the CLI twice is
        harmless.  A previously REVOKED grant is not resurrected - a new row is
        written, so the revocation stays in the history and a later audit can
        see that access was withdrawn and then granted again.

        The one-active-grant invariant is ALSO enforced by a partial unique
        index (`ux_patient_access_user_patient_active`), because the check above
        is a check-then-insert and therefore racy.  This method catches the
        resulting `IntegrityError` and re-reads, so the CLI reports the
        existing grant instead of a constraint-violation traceback.
        """
        if relationship not in AccessRelationship.ALL:
            raise ValueError(
                f"Unknown relationship {relationship!r}. Expected one of "
                f"{sorted(AccessRelationship.ALL)}."
            )

        existing = self._active_grant(user.id, patient_id)
        if existing is not None:
            return existing

        grant = PatientAccess(
            user_id=user.id,
            patient_id=patient_id,
            relationship=relationship,
        )
        self._db.add(grant)
        try:
            self._db.commit()
        except IntegrityError:
            # Lost the race: another grant for this pair landed first.
            self._db.rollback()
            concurrent = self._active_grant(user.id, patient_id)
            if concurrent is not None:
                return concurrent
            raise
        self._db.refresh(grant)
        logger.info(
            "access_granted user_id=%s patient_id=%s relationship=%s",
            user.id,
            patient_id,
            relationship,
        )
        return grant

    def revoke_access(self, user: AppUser, patient_id: uuid.UUID) -> bool:
        """
        Revoke every active grant between `user` and `patient_id`.

        Returns True when something was actually revoked.  The row is retained
        with `revoked_at` set rather than deleted; see `app.models.user`.
        """
        grants = list(
            self._db.execute(
                select(PatientAccess).where(
                    PatientAccess.user_id == user.id,
                    PatientAccess.patient_id == patient_id,
                    PatientAccess.revoked_at.is_(None),
                )
            ).scalars().all()
        )
        if not grants:
            return False

        from app.core.timezones import utcnow

        now = utcnow()
        for grant in grants:
            grant.revoked_at = now
        self._db.commit()
        logger.info(
            "access_revoked user_id=%s patient_id=%s count=%s",
            user.id,
            patient_id,
            len(grants),
        )
        return True

    # â”€â”€ Internals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _active_grant(
        self, user_id: uuid.UUID, patient_id: uuid.UUID
    ) -> Optional[PatientAccess]:
        """
        The active grant, or None.

        Scoped to `revoked_at IS NULL` on every read, which is what makes a
        revocation effective on the next request rather than whenever a cached
        decision happens to expire.
        """
        return self._db.execute(
            select(PatientAccess).where(
                PatientAccess.user_id == user_id,
                PatientAccess.patient_id == patient_id,
                PatientAccess.revoked_at.is_(None),
            )
        ).scalars().first()
