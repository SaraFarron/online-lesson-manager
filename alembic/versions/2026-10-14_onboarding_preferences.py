"""Add onboarding preferences and unique invitation codes.

Revision ID: 8bb41e3a1002
Revises: 8bb41e3a1001
"""

import re

import sqlalchemy as sa
from alembic import op

revision = "8bb41e3a1002"
down_revision = "8bb41e3a1001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, code FROM teacher_profile")
    ).all()
    seen: set[str] = set()
    for profile_id, code in rows:
        if (
            not isinstance(code, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,56}", code) is None
            or code in seen
        ):
            raise RuntimeError(
                f"Invalid or duplicate teacher code on profile {profile_id}. "
                "Resolve invitation codes explicitly before retrying; "
                "no codes were replaced."
            )
        seen.add(code)

    with op.batch_alter_table("teacher_profile") as batch:
        batch.add_column(
            sa.Column(
                "working_days", sa.JSON(), nullable=False,
                server_default=sa.text("'[1,2,3,4,5]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "max_consecutive_lessons", sa.Integer(),
                nullable=False, server_default="4",
            )
        )
        batch.create_unique_constraint("teacher_profile_code_key", ["code"])

    # Owner approved resetting existing notification preferences (Task 00).
    connection.execute(sa.text('UPDATE "user" SET notification_lessons_today = 540'))
    connection.execute(
        sa.text(
            "UPDATE student_profile SET notification_lesson = 15, "
            "notification_homework = 2"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("teacher_profile") as batch:
        batch.drop_constraint("teacher_profile_code_key", type_="unique")
        batch.drop_column("max_consecutive_lessons")
        batch.drop_column("working_days")
