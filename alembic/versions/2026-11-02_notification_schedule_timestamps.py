"""Track schedule-notification preference and routing changes.

Revision ID: 20261102notify2
Revises: 20261101notify1
"""

import sqlalchemy as sa

from alembic import op

revision = "20261102notify2"
down_revision = "20261101notify1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(
            sa.Column(
                "notification_schedule_updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )

    with op.batch_alter_table("student_profile") as batch:
        batch.add_column(
            sa.Column(
                "notification_lesson_updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("student_profile") as batch:
        batch.drop_column("notification_lesson_updated_at")

    with op.batch_alter_table("user") as batch:
        batch.drop_column("notification_schedule_updated_at")
