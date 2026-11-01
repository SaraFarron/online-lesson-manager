"""Add durable notification intents and independent homework push toggles.

Revision ID: 20261101notify1
Revises: c4e6a8f19d23
"""

import sqlalchemy as sa

from alembic import op

revision = "20261101notify1"
down_revision = "c4e6a8f19d23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("student_profile") as batch:
        batch.add_column(
            sa.Column(
                "notification_homework_assignment",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    with op.batch_alter_table("teacher_profile") as batch:
        batch.add_column(
            sa.Column(
                "notification_homework_submission",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    op.create_table(
        "notification_intent",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("notification_type", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("source_version", sa.Integer(), nullable=True),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("occurrence_source_type", sa.String(length=24), nullable=True),
        sa.Column("occurrence_source_id", sa.Uuid(), nullable=True),
        sa.Column("occurrence_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "state", sa.String(length=16), server_default="PENDING", nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(length=16), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "notification_type IN "
            "('DAILY_DIGEST', 'LESSON_REMINDER', 'HOMEWORK_REMINDER', "
            "'ASSIGNMENT_CREATED', 'SUBMISSION_RECEIVED')",
            name=op.f("notification_intent_notification_type_valid_check"),
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'CLAIMED', 'RETRY', 'SENT', 'TERMINAL', 'SUPPRESSED')",
            name=op.f("notification_intent_state_valid_check"),
        ),
        sa.CheckConstraint(
            "last_outcome IS NULL OR last_outcome IN "
            "('SENT', 'RETRYABLE', 'TERMINAL', 'UNKNOWN')",
            name=op.f("notification_intent_last_outcome_valid_check"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("notification_intent_attempt_count_nonnegative_check"),
        ),
        sa.CheckConstraint(
            "(state = 'CLAIMED' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state != 'CLAIMED' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name=op.f("notification_intent_lease_state_consistent_check"),
        ),
        sa.CheckConstraint(
            "expires_at > scheduled_at",
            name=op.f("notification_intent_expiry_after_schedule_check"),
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"],
            ["user.id"],
            name="notification_intent_recipient_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="notification_intent_pkey"),
        sa.UniqueConstraint("dedupe_key", name="notification_intent_dedupe_key_key"),
    )
    op.create_index(
        "ix_notification_intent_state_available_at",
        "notification_intent",
        ["state", "available_at"],
    )
    op.create_index(
        "ix_notification_intent_state_lease_expires_at",
        "notification_intent",
        ["state", "lease_expires_at"],
    )
    op.create_index(
        "ix_notification_intent_recipient_id_created_at",
        "notification_intent",
        ["recipient_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    pending_intents = connection.execute(
        sa.text("SELECT COUNT(*) FROM notification_intent")
    ).scalar_one()
    disabled_assignment_toggles = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM student_profile "
            "WHERE notification_homework_assignment = false"
        )
    ).scalar_one()
    disabled_submission_toggles = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM teacher_profile "
            "WHERE notification_homework_submission = false"
        )
    ).scalar_one()
    if pending_intents or disabled_assignment_toggles or disabled_submission_toggles:
        raise RuntimeError(
            "Cannot downgrade while notification intents or disabled homework "
            "push preferences exist. Preserve or resolve this data before "
            "retrying; no data was modified."
        )

    op.drop_index(
        "ix_notification_intent_recipient_id_created_at",
        table_name="notification_intent",
    )
    op.drop_index(
        "ix_notification_intent_state_lease_expires_at",
        table_name="notification_intent",
    )
    op.drop_index(
        "ix_notification_intent_state_available_at",
        table_name="notification_intent",
    )
    op.drop_table("notification_intent")

    with op.batch_alter_table("teacher_profile") as batch:
        batch.drop_column("notification_homework_submission")
    with op.batch_alter_table("student_profile") as batch:
        batch.drop_column("notification_homework_assignment")
