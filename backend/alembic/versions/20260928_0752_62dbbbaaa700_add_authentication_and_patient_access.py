"""
add authentication and patient access

Introduces the two tables that make authorization possible.  Before this
revision every `patients` row and every clinical row referencing one was
reachable by anyone who could present any request at all: there was no
principal, so there was nothing to check a grant against.

  app_users      the authenticated principal.  A login, not a role.
  patient_access the grant: this user may act on this patient.

Migration notes
---------------
* `relationship` is a String with a CHECK constraint rather than a PostgreSQL
  enum.  One column does not justify a `CREATE TYPE` whose creation order would
  have to be reasoned about during rollback, and a CHECK constraint is the
  stronger guarantee anyway: unlike a Python-side constant it still holds for a
  direct `INSERT` from psql, a migration, or a future service.

* `app_users.email` is UNIQUE but case-SENSITIVE.  Uniqueness is not declared
  case-insensitively here because normalizing case is the writer's job (see
  `app/cli/manage_access.py`, which lowercases and strips before insert).
  Normalizing in one place beats hiding it in a functional index, because the
  stored value then matches what the user typed and what a support lookup
  searches for.

* `ux_patient_access_user_patient_active` is a PARTIAL unique index, and it is
  the reason this is not a plain UNIQUE(user_id, patient_id).  A revoked grant
  is retained for audit, so re-granting the same pair later must be able to
  insert a second row.  The partial predicate makes uniqueness apply only to
  live grants, and the same index serves the per-request authorization lookup
  because that query filters on exactly `revoked_at IS NULL`.

  Enforcing "one active grant" here rather than only in the service layer closes
  a check-then-insert race that would otherwise leave two live grants for one
  pair - after which "revoke that grant" has no single obvious target.

Downgrade
---------
`downgrade()` DOES run, and it drops `patient_access` and `app_users` outright.
That is a deliberate, destructive rollback rather than a refused one: a
half-reverted authorization schema is worse than no rollback at all, because
every existing principal and grant would be gone and the deployment would
either refuse every request or - far worse - serve patients to anyone.

The loss is NOT recoverable by re-upgrading. Every account and the entire
grant history, including revoked grants, are destroyed, and re-running the
upgrade recreates empty tables. Back up before downgrading past this revision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "62dbbbaaa700"
down_revision: Union[str, None] = "e6f1a2b4c7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        # Off unless an operator sets it.  The only global privilege, needed
        # for the two endpoints that act on every patient at once.
        sa.Column(
            "system_access",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "patient_access",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column(
            "relationship",
            sa.String(length=32),
            server_default=sa.text("'self'"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # NULL means active.  Every authorization query filters on this being
        # NULL, so a revoked grant is inert everywhere without being deleted.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "relationship IN ('self', 'caregiver', 'care_team')",
            name="ck_patient_access_relationship",
        ),
        # CASCADE both ways: a grant is meaningless without its user, and a
        # grant to a deleted patient has nothing left to authorize.
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ux_patient_access_user_patient_active",
        "patient_access",
        ["user_id", "patient_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_patient_access_user_patient_active",
        table_name="patient_access",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("patient_access")
    op.drop_table("app_users")
