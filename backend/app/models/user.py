"""
CareLoop AI — Authentication & Patient Access ORM Models

This module provides the two tables that turn a bearer token into a set of
patients a caller is allowed to touch.  It is deliberately the smallest model
that can answer the only question authorization actually asks:

    "may this authenticated principal act on this patient's record?"

WHAT EXISTS, AND WHY NOTHING MORE
--------------------------------
There was no principal and no link from a caller to a patient before this
module: `patients` rows were reachable by anyone who could guess or obtain a
UUID, and `caregiver_contact` is a phone number rather than an account, so it
could not carry an identity.  Two tables are the minimum that can express a
grant:

  * `app_users`      - the authenticated principal (a login, not a role).
  * `patient_access` - the grant: this user may act on this patient, in this
                       relationship.

THE ROLE LIVES ON THE GRANT, NOT ON THE USER
--------------------------------------------
`patient_access.relationship` describes the user *for that patient*, and there
is deliberately NO global `role` column on `app_users`.  The same human is
`self` for their own record and `caregiver` for a relative's; a clinician is
`care_team` for the patients they follow.  A global role would be a second
source of truth that can contradict the grant and silently escalate access -
and a global role cannot express "caregiver of exactly one patient" at all,
which is the case that matters most for third-party access to health data.

`relationship` is recorded for audit and for future differentiation.  It
deliberately does NOT restrict operations today: any active grant allows read
and write on that patient's records.  Adding a capability matrix is a real
requirement for a care team acting under delegated authority, but it is NOT
this phase's job, and half-implementing it would be worse than not having it -
see README "Authorization model" for what is and is not enforced.

`system_access` is the one global privilege, and it exists because two
endpoints act on EVERY patient at once (`POST /notifications/dispatch`,
`POST /notifications/retry`).  Per-patient grants cannot express permission to
do that, so it is an explicit boolean rather than a hidden convention: the
default is False, and it is only ever set by an operator.

NO API ROUTE CAN WIDEN OR REVOKE A GRANT
----------------------------------------
A caller who could mint their own `care_team` grant could read every patient
in the system, so grant management is an operator action (see
`app/cli/manage_access.py`).  The single exception is patient self-
registration (`POST /api/v1/auth/register`), which grants exactly one `self`
grant to the patient record it itself just created.  That is safe for one
reason: the patient id is minted server-side by the route and never taken from
the caller, so the grant cannot point at anybody else.  Everything else -
widening a grant, revoking one, or minting `care_team`/`caregiver` grants -
remains outside the HTTP surface.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccessRelationship:
    """
    The closed set of relationships a grant can express.

    Plain strings rather than a PostgreSQL enum: this is one column on one
    table, the set is validated by a CHECK constraint as well as here, and a
    string keeps the security migration to a single revision with no
    CREATE-TYPE ordering to get wrong.  A CHECK constraint is a stronger
    guarantee than a Python constant anyway, because it still holds for a
    direct SQL write.
    """

    #: The patient acting on their own record.
    SELF = "self"

    #: A relative or other supporter the patient has authorized.  Carries the
    #: same read/write reach as `self`; see the module docstring.
    CAREGIVER = "caregiver"

    #: A clinician or other member of the care team.
    CARE_TEAM = "care_team"

    ALL = frozenset({SELF, CAREGIVER, CARE_TEAM})


class AppUser(Base):
    """
    An authenticated principal.

    Not a patient and not a clinician - a login.  A user's authority comes
    entirely from `patient_access` rows, so creating a user grants nothing on
    its own.
    """

    __tablename__ = "app_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # 320 is the RFC 5321 maximum length of a forward-path email address, so
    # this column cannot be the reason a legitimate address is rejected.
    # Stored lowercased and stripped: email local-parts are technically
    # case-sensitive, but in practice a mismatch between "Alice@" and
    # "alice@" would lock a person out of their own record, so one canonical
    # form is the safer failure.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    # Display name for the account, captured at self-registration.  Separate
    # from the patient's `name` so an account can exist before (or without) a
    # patient record; operator-created accounts leave it as an empty string.
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
        server_default="",
    )

    # A bcrypt digest, never a password.  The column is named for the fact
    # rather than the algorithm so the hashing scheme can be upgraded without a
    # rename; `app.core.security` owns that choice.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Deactivation is the blunt instrument for a compromised or departed
    # account: it takes effect on the next request, because the dependency
    # re-reads the row rather than trusting the token's claims.  Revoking
    # individual grants is the surgical alternative, and is what
    # `patient_access.revoked_at` is for.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    # See the module docstring: the only global privilege, needed solely for the
    # two whole-system notification endpoints.  Off unless an operator says so.
    system_access: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        # Email only.  A user row has no clinical content, but logging a
        # principal's identifier is still an identity disclosure, and
        # __repr__ is the one representation that reaches logs by accident.
        return f"<AppUser id={self.id}>"


class PatientAccess(Base):
    """
    A grant: this user may act on this patient's records.

    A revoked grant is RETAINED with `revoked_at` set rather than deleted.
    Deleting it would make "why did this person still have access last Tuesday"
    unanswerable, and the answer is exactly what a clinical privacy review asks
    for.  Revocation is enforced by every query (`revoked_at IS NULL`), so a
    retained row grants nothing.
    """

    __tablename__ = "patient_access"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # CASCADE: a grant is meaningless without its user, and there is no privacy
    # reason to retain a link to a deleted account.
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )

    relationship: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AccessRelationship.SELF,
        server_default=AccessRelationship.SELF,
    )

    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # NULL means active.  Every authorization query filters on this being NULL,
    # so a revoked grant is inert everywhere without needing to be deleted.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "relationship IN ('self', 'caregiver', 'care_team')",
            name="ck_patient_access_relationship",
        ),
        # "One ACTIVE grant per (user, patient)" as a PARTIAL unique index rather
        # than a table-level UNIQUE constraint, because a revoked grant is
        # retained: a re-grant legitimately produces a second row for the same
        # pair, which a plain UNIQUE(user_id, patient_id) would forbid and make
        # re-granting after revocation impossible.
        #
        # Enforcing this in the database rather than only in the service layer
        # is deliberate.  A service-level check-then-insert is a race: two
        # concurrent grants for the same pair both see no active row and both
        # insert.  That race is not cosmetic - duplicate active grants are two
        # independent paths to the same record, and revoking "the" grant later
        # has no single obvious target.  The partial index makes the invariant
        # hold under any write path, including direct SQL.
        #
        # It doubles as the index that makes the authorization lookup a plain
        # index scan, since every authorization query filters on exactly the
        # same predicate: `revoked_at IS NULL`.
        Index(
            "ux_patient_access_user_patient_active",
            "user_id",
            "patient_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PatientAccess user_id={self.user_id} "
            f"patient_id={self.patient_id} relationship={self.relationship!r}>"
        )
