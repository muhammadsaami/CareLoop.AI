"""
add self-service authentication fields (Phase 8A)

Patient self-registration (`POST /api/v1/auth/register`) needs accurate account
identity and may legitimately create a patient with no phone number, neither of
which the schema expressed before this revision:

* `app_users.full_name`    - the account's display name, captured at
  registration.  Empty string for operator-created accounts, which predate it.

* `patients.contact_number` - now NULLABLE.  Registration supplies only
  `{full_name, email, password}`, so the patient row it creates has no phone
  number to store; the notification layer already treats a missing contact as
  "not deliverable", so a null here cannot silently drop a reminder.

Migration notes
---------------
`contact_number` was NOT NULL but carries no index or constraint beyond that,
so relaxing it is a metadata-only change on PostgreSQL (no table rewrite).
The downgrade backfills NULLs with an empty string - a placeholder rather than
the patient's real number, which cannot be recovered - before re-imposing the
NOT NULL constraint.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5f7a9c1e3b2d"
down_revision: Union[str, None] = "62dbbbaaa700"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing operator-created accounts get an empty display name; the value
    # is a server default so the metadata-only ADD COLUMN is not a rewrite.
    op.add_column(
        "app_users",
        sa.Column(
            "full_name",
            sa.String(length=255),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )

    op.alter_column(
        "patients",
        "contact_number",
        existing_type=sa.String(length=50),
        nullable=True,
    )


def downgrade() -> None:
    # Backfill NULLs before re-imposing NOT NULL.  There is no recoverable
    # original value for a self-registered patient, so the empty string is a
    # placeholder, not a restoration.
    op.execute(
        "UPDATE patients SET contact_number = '' WHERE contact_number IS NULL"
    )
    op.alter_column(
        "patients",
        "contact_number",
        existing_type=sa.String(length=50),
        nullable=False,
    )

    op.drop_column("app_users", "full_name")