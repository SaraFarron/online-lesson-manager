"""Persist confirmed student removal operations for idempotent retries.

Revision ID: 20261104removal
Revises: 20261103profiles
"""

import sqlalchemy as sa

from alembic import op

revision = "20261104removal"
down_revision = "20261103profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_removal_operation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("relationship_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.Uuid(), nullable=False),
        sa.Column("confirmation_token", sa.Text(), nullable=False),
        sa.Column("impact", sa.JSON(), nullable=False),
        sa.Column("staged_attachment_ids", sa.JSON(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["relationship_id", "teacher_id", "student_id"],
            [
                "student_teacher_relationship.id",
                "student_teacher_relationship.teacher_id",
                "student_teacher_relationship.student_id",
            ],
            name="student_removal_operation_relationship_scope_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="student_removal_operation_pkey"),
        sa.UniqueConstraint(
            "confirmation_token",
            name="student_removal_operation_confirmation_token_key",
        ),
        sa.UniqueConstraint(
            "teacher_id",
            "operation_key",
            name="student_removal_operation_teacher_operation_key_key",
        ),
    )
    op.create_index(
        "ix_student_removal_operation_teacher_id_student_id_created_at",
        "student_removal_operation",
        ["teacher_id", "student_id", "created_at"],
    )


def downgrade() -> None:
    operation_count = (
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM student_removal_operation"))
        .scalar_one()
    )
    if operation_count:
        raise RuntimeError(
            "Cannot downgrade while confirmed student removal operation records "
            "exist; preserve idempotency and cleanup recovery data first."
        )

    op.drop_index(
        "ix_student_removal_operation_teacher_id_student_id_created_at",
        table_name="student_removal_operation",
    )
    op.drop_table("student_removal_operation")
