"""add phase 6 enum values

Revision ID: b2c9d84f1a63
Revises: ca9da7f0d454
Create Date: 2026-09-28 09:10:00.000000+00:00

Deliberately separate from the table migration that follows it.

Postgres refuses to USE an enum value added earlier in the same transaction:
`ALTER TYPE ... ADD VALUE` takes effect only on commit, so an index predicate
like `WHERE reminder_type = 'checkin'` fails with
`unsafe use of new value "checkin" of enum type reminder_type`. Casting the
column to text does not help - the cast still needs the new value to exist.

Splitting the migration is the supported way out. The values land and commit
here; the next migration creates the tables and indexes that reference them.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b2c9d84f1a63'
down_revision: Union[str, None] = 'ca9da7f0d454'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both types are Phase 5, extended rather than replaced, so historical rows
    # keep their original values. No row written by this migration uses the new
    # values, so nothing needs invalidating.
    op.execute("ALTER TYPE notification_type ADD VALUE 'checkin_prompt'")
    op.execute("ALTER TYPE notification_type ADD VALUE 'escalation_notice'")
    op.execute("ALTER TYPE reminder_type ADD VALUE 'checkin'")


def downgrade() -> None:
    # Postgres cannot remove an enum value, so these cannot be undone by this
    # migration. `downgrade` of the table migration rebuilds both types without
    # the Phase 6 labels, which is where the removal actually happens; doing it
    # in one place avoids two migrations each half-rebuilding the type.
    #
    # The labels are deliberately left in place here, and a deployment that
    # downgrades is expected to continue to the next revision.
    pass
