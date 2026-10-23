"""Add per-teacher schedule mutation guard rows.

Revision ID: c61e7b0a4d29
Revises: 6b87556d4a21
"""

import sqlalchemy as sa

from alembic import op

revision = "c61e7b0a4d29"
down_revision = "6b87556d4a21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teacher_schedule_guard",
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["user.id"],
            name="teacher_schedule_guard_teacher_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("teacher_id", name="teacher_schedule_guard_pkey"),
    )


def downgrade() -> None:
    op.drop_table("teacher_schedule_guard")
