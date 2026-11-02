"""Track changes to incomplete-homework reminder preferences.

Revision ID: 20261103notify3
Revises: 20261102notify2
"""

import sqlalchemy as sa

from alembic import op

revision = "20261103notify3"
down_revision = "20261102notify2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("student_profile") as batch:
        batch.add_column(
            sa.Column(
                "notification_homework_updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("student_profile") as batch:
        batch.drop_column("notification_homework_updated_at")
