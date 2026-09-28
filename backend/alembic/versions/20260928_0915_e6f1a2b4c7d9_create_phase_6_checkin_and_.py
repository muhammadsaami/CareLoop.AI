"""create phase 6 check-in and escalation tables

Revision ID: e6f1a2b4c7d9
Revises: b2c9d84f1a63
Create Date: 2026-09-28 09:15:00.000000+00:00

Follows `b2c9d84f1a63`, which commits the Phase 6 enum values. The split is
required by Postgres, not stylistic: an enum value added in the same
transaction cannot be referenced by an index predicate. See that revision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e6f1a2b4c7d9'
down_revision: Union[str, None] = 'b2c9d84f1a63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every Phase 6 enum type is created with raw SQL above, so every column that
# references one is declared with `create_type=False`. Without that flag
# SQLAlchemy emits its own CREATE TYPE while rendering the table, and Postgres
# rejects the duplicate - "type escalation_category already exists". The flag
# makes this migration the single owner of the type, which is also what lets
# `downgrade` drop it unconditionally.
CHECKIN_STATUS = postgresql.ENUM(
    'completed', 'escalated', 'needs_review',
    name='checkin_status', create_type=False,
)
ESCALATION_STATUS = postgresql.ENUM(
    'pending', 'notified', 'acknowledged', 'resolved', 'cancelled',
    name='escalation_status', create_type=False,
)
ESCALATION_CATEGORY = postgresql.ENUM(
    'warning_criteria_changed', 'warning_criteria_present',
    name='escalation_category', create_type=False,
)
ESCALATION_WORKFLOW = postgresql.ENUM(
    'review_by_care_team', 'contact_care_team',
    name='escalation_workflow', create_type=False,
)


def upgrade() -> None:
    # ── New enum types ────────────────────────────────────────────────────────
    # Created explicitly rather than left to `create_table`, because every
    # column referencing one is declared with `create_type=False` above and
    # this migration is therefore their only creator.
    op.execute("CREATE TYPE checkin_status AS ENUM "
               "('completed', 'escalated', 'needs_review')")
    op.execute("CREATE TYPE escalation_status AS ENUM "
               "('pending', 'notified', 'acknowledged', 'resolved', "
               "'cancelled')")
    op.execute("CREATE TYPE escalation_category AS ENUM "
               "('warning_criteria_changed', 'warning_criteria_present')")
    op.execute("CREATE TYPE escalation_workflow AS ENUM "
               "('review_by_care_team', 'contact_care_team')")

    # ── checkins: structured evaluation columns ───────────────────────────────
    op.add_column('checkins', sa.Column('responses', sa.JSON(), nullable=True))
    # NOT NULL with a server default: existing Phase 1 rows have no evaluated
    # status, and the honest value for a free-text row that no rule set has
    # ever seen is `completed` - it is not a claim that anything was evaluated,
    # because `responses` stays NULL and Phase 1 submissions still read
    # `response_text`.
    op.add_column(
        'checkins',
        sa.Column(
            'status',
            CHECKIN_STATUS,
            server_default='completed',
            nullable=False,
        ),
    )
    op.add_column('checkins', sa.Column('timezone', sa.String(length=64),
                                         nullable=True))
    op.add_column('checkins', sa.Column('reminder_id', sa.UUID(), nullable=True))
    op.add_column(
        'checkins',
        sa.Column(
            'needs_review',
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column('checkins', sa.Column('review_reason',
                                         sa.String(length=64), nullable=True))
    op.add_column('checkins', sa.Column('completed_at',
                                         sa.DateTime(timezone=True),
                                         nullable=True))
    op.add_column(
        'checkins',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )
    # Phase 1 declared `response_text` NOT NULL. It becomes nullable so a
    # Phase 6 check-in can record a structured answer set with no prose at all.
    op.alter_column('checkins', 'response_text', existing_type=sa.Text(),
                    nullable=True)
    op.create_foreign_key(
        'fk_checkins_reminder_id', 'checkins', 'reminders',
        ['reminder_id'], ['id'], ondelete='SET NULL',
    )

    # `ix_checkins_patient_id` and `ix_checkins_date` were created by the Phase 1
    # migration and are not repeated here: Postgres has no `CREATE INDEX IF NOT
    # EXISTS` on indexes an earlier migration owns, and a bare `CREATE INDEX`
    # against a name that already exists fails the whole transaction.
    op.create_index('ix_checkins_patient_id_date', 'checkins',
                    ['patient_id', 'date'], unique=False)
    op.create_index('ix_checkins_status', 'checkins', ['status'], unique=False)
    # THE one-check-in-per-day guard. A retried request, a double tap, or two
    # workers racing is settled here rather than by application-level hope.
    op.create_index('uq_checkins_patient_date', 'checkins',
                    ['patient_id', 'date'], unique=True)

    # ── escalations ──────────────────────────────────────────────────────────
    op.create_table(
        'escalations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('patient_id', sa.UUID(), nullable=False),
        sa.Column('checkin_id', sa.UUID(), nullable=False),
        sa.Column('rule_code', sa.String(length=64), nullable=False),
        sa.Column('rule_version', sa.String(length=32), nullable=False),
        sa.Column('category', ESCALATION_CATEGORY, nullable=False),
        sa.Column('workflow', ESCALATION_WORKFLOW, nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=True),
        sa.Column('warning_symptom_id', sa.UUID(), nullable=True),
        sa.Column('reason_code', sa.String(length=128), nullable=False),
        sa.Column(
            'status',
            ESCALATION_STATUS,
            server_default='pending',
            nullable=False,
        ),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.String(length=255), nullable=True),
        sa.Column('notification_id', sa.UUID(), nullable=True),
        sa.Column('notification_blocked_reason', sa.String(length=64),
                  nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['checkin_id'], ['checkins.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'],
                                ondelete='RESTRICT'),
        # The escalation is the clinical record; the notification is the delivery
        # attempt. Losing the pointer to a deleted notification is acceptable;
        # losing the escalation is not.
        sa.ForeignKeyConstraint(['warning_symptom_id'],
                                ['warning_symptoms.id'],
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    # THE idempotency guarantee: one escalation per (check-in, rule).
    op.create_index('uq_escalations_checkin_rule', 'escalations',
                    ['checkin_id', 'rule_code'], unique=True)
    op.create_index('ix_escalations_patient_id', 'escalations', ['patient_id'],
                    unique=False)
    op.create_index('ix_escalations_status', 'escalations', ['status'],
                    unique=False)
    op.create_index('ix_escalations_patient_id_created_at', 'escalations',
                    ['patient_id', 'created_at'], unique=False)
    op.create_index('ix_escalations_checkin_id', 'escalations', ['checkin_id'],
                    unique=False)
    op.create_index('ix_escalations_notification_id', 'escalations',
                    ['notification_id'], unique=False)

    # ── notifications: link an escalation notice to its escalation ────────────
    op.add_column('notifications', sa.Column('escalation_id', sa.UUID(),
                                             nullable=True))
    op.create_foreign_key(
        'fk_notifications_escalation_id', 'notifications', 'escalations',
        ['escalation_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_notifications_escalation_id', 'notifications',
                    ['escalation_id'], unique=False)

    # ── reminders: the daily check-in prompt slot ────────────────────────────
    # One check-in prompt per patient per local time. Partial, because the
    # Phase 5 medication slot index is scoped to medication rows and this one
    # must not collide with them - `reminder_type` is part of the predicate so
    # a medication reminder and a check-in prompt at 09:00 are both legal.
    op.create_index(
        'uq_reminders_checkin_slot',
        'reminders',
        ['patient_id', 'local_time'],
        unique=True,
        postgresql_where=sa.text("reminder_type = 'checkin'"),
    )


def downgrade() -> None:
    op.drop_index('uq_reminders_checkin_slot', table_name='reminders')
    op.drop_index('ix_notifications_escalation_id', table_name='notifications')
    op.drop_constraint('fk_notifications_escalation_id', 'notifications',
                       type_='foreignkey')
    op.drop_column('notifications', 'escalation_id')

    op.drop_index('ix_escalations_notification_id', table_name='escalations')
    op.drop_index('ix_escalations_checkin_id', table_name='escalations')
    op.drop_index('ix_escalations_patient_id_created_at',
                  table_name='escalations')
    op.drop_index('ix_escalations_status', table_name='escalations')
    op.drop_index('ix_escalations_patient_id', table_name='escalations')
    op.drop_index('uq_escalations_checkin_rule', table_name='escalations')
    op.drop_table('escalations')

    op.drop_constraint('fk_checkins_reminder_id', 'checkins',
                       type_='foreignkey')
    op.drop_index('uq_checkins_patient_date', table_name='checkins')
    op.drop_index('ix_checkins_status', table_name='checkins')
    op.drop_index('ix_checkins_patient_id_date', table_name='checkins')
    # `ix_checkins_patient_id` and `ix_checkins_date` belong to Phase 1 and are
    # left in place, exactly as the upgrade left them.
    op.drop_column('checkins', 'updated_at')
    op.drop_column('checkins', 'completed_at')
    op.drop_column('checkins', 'review_reason')
    op.drop_column('checkins', 'needs_review')
    op.drop_column('checkins', 'reminder_id')
    op.drop_column('checkins', 'timezone')
    op.drop_column('checkins', 'status')
    op.drop_column('checkins', 'responses')
    op.alter_column('checkins', 'response_text', existing_type=sa.Text(),
                    nullable=False)

    # Postgres cannot drop an enum value, so the Phase 5 types are rebuilt
    # without the Phase 6 labels rather than left holding values no model
    # declares. Rebuilding rather than recreating preserves the column type for
    # every dependent table.
    #
    # The `ALTER TYPE ... RENAME` dance is the standard way to swap an enum
    # type in place: build the new type under a temporary name, point the
    # columns at it, drop the old, rename the new one back.
    op.execute("ALTER TYPE notification_type RENAME TO notification_type_p5")
    op.execute("CREATE TYPE notification_type AS ENUM "
               "('medication_reminder', 'appointment_reminder')")
    op.execute("ALTER TABLE notifications ALTER COLUMN notification_type "
               "TYPE notification_type USING notification_type::text::"
               "notification_type")
    op.execute("DROP TYPE notification_type_p5")

    op.execute("ALTER TYPE reminder_type RENAME TO reminder_type_p5")
    op.execute("CREATE TYPE reminder_type AS ENUM "
               "('medication', 'appointment')")
    op.execute("ALTER TABLE reminders ALTER COLUMN reminder_type "
               "TYPE reminder_type USING reminder_type::text::reminder_type")
    op.execute("DROP TYPE reminder_type_p5")

    # Enum types for columns that no longer exist once the table is gone.
    op.execute("DROP TYPE IF EXISTS escalation_category")
    op.execute("DROP TYPE IF EXISTS escalation_workflow")
    op.execute("DROP TYPE IF EXISTS escalation_status")
    op.execute("DROP TYPE IF EXISTS checkin_status")
