"""Add durable teacher broadcasts to the notification queue.

Revision ID: 20261102broadcast
Revises: 20261103notify3
"""

import sqlalchemy as sa

from alembic import op

revision = "20261102broadcast"
down_revision = "20261103notify3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_intent") as batch:
        batch.drop_constraint(
            batch.f("notification_intent_notification_type_valid_check"),
            type_="check",
        )
        batch.create_check_constraint(
            batch.f("notification_intent_notification_type_valid_check"),
            "notification_type IN "
            "('DAILY_DIGEST', 'LESSON_REMINDER', 'HOMEWORK_REMINDER', "
            "'ASSIGNMENT_CREATED', 'SUBMISSION_RECEIVED', 'TEACHER_BROADCAST')",
        )

    op.create_table(
        "teacher_broadcast",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.Uuid(), nullable=False),
        sa.Column("confirmation_token", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("unsendable_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "recipient_count >= 0 AND eligible_count >= 0 "
            "AND unsendable_count >= 0 "
            "AND recipient_count = eligible_count + unsendable_count",
            name=op.f("teacher_broadcast_recipient_counts_consistent_check"),
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["user.id"],
            name="teacher_broadcast_teacher_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="teacher_broadcast_pkey"),
        sa.UniqueConstraint(
            "confirmation_token",
            name="teacher_broadcast_confirmation_token_key",
        ),
        sa.UniqueConstraint(
            "teacher_id",
            "operation_key",
            name="teacher_broadcast_teacher_operation_key_key",
        ),
    )
    op.create_index(
        "ix_teacher_broadcast_teacher_id_created_at",
        "teacher_broadcast",
        ["teacher_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    broadcast_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM teacher_broadcast")
    ).scalar_one()
    broadcast_intent_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM notification_intent "
            "WHERE notification_type = 'TEACHER_BROADCAST'"
        )
    ).scalar_one()
    if broadcast_count or broadcast_intent_count:
        raise RuntimeError(
            "Cannot downgrade while teacher broadcast publications or notification "
            "intents exist. Preserve or resolve this data before retrying; no data "
            "was modified."
        )

    op.drop_index(
        "ix_teacher_broadcast_teacher_id_created_at",
        table_name="teacher_broadcast",
    )
    op.drop_table("teacher_broadcast")

    with op.batch_alter_table("notification_intent") as batch:
        batch.drop_constraint(
            batch.f("notification_intent_notification_type_valid_check"),
            type_="check",
        )
        batch.create_check_constraint(
            batch.f("notification_intent_notification_type_valid_check"),
            "notification_type IN "
            "('DAILY_DIGEST', 'LESSON_REMINDER', 'HOMEWORK_REMINDER', "
            "'ASSIGNMENT_CREATED', 'SUBMISSION_RECEIVED')",
        )
