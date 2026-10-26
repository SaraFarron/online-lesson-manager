"""Add homework, submission, and attachment metadata.

Revision ID: d2f0409b6e21
Revises: a73d0c4b9e21
"""

import sqlalchemy as sa

from alembic import op

revision = "d2f0409b6e21"
down_revision = "a73d0c4b9e21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "homework",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("lesson_kind", sa.String(length=16), nullable=True),
        sa.Column("lesson_source_id", sa.Uuid(), nullable=True),
        sa.Column("lesson_occurrence_start", sa.DateTime(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="INCOMPLETE",
            nullable=False,
        ),
        sa.Column("completion_source", sa.String(length=32), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operation_key", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cleanup_pending",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "text IS NULL OR length(trim(text)) > 0",
            name=op.f("homework_text_nonempty_check"),
        ),
        sa.CheckConstraint(
            "(lesson_kind IS NULL AND lesson_source_id IS NULL "
            "AND lesson_occurrence_start IS NULL) OR "
            "(lesson_kind IN ('EVENT', 'RECURRENT_EVENT') "
            "AND lesson_source_id IS NOT NULL "
            "AND lesson_occurrence_start IS NOT NULL)",
            name=op.f("homework_lesson_occurrence_consistent_check"),
        ),
        sa.CheckConstraint(
            "(status = 'INCOMPLETE' AND completed_at IS NULL "
            "AND completion_source IS NULL) OR "
            "(status = 'COMPLETE' AND completed_at IS NOT NULL "
            "AND completion_source IN ('STUDENT_SUBMISSION', 'TEACHER'))",
            name=op.f("homework_completion_consistent_check"),
        ),
        sa.CheckConstraint(
            "status IN ('INCOMPLETE', 'COMPLETE')",
            name=op.f("homework_status_valid_check"),
        ),
        sa.CheckConstraint(
            "NOT cleanup_pending OR deleted_at IS NOT NULL",
            name=op.f("homework_cleanup_requires_deletion_check"),
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["user.id"],
            name="homework_teacher_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["user.id"],
            name="homework_student_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="homework_pkey"),
        sa.UniqueConstraint(
            "teacher_id",
            "operation_key",
            name="homework_teacher_id_operation_key_key",
        ),
    )
    op.create_index(
        "homework_teacher_created_idx",
        "homework",
        ["teacher_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "homework_student_created_idx",
        "homework",
        ["student_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "homework_submission",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("homework_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("operation_key", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version > 0", name=op.f("homework_submission_version_positive_check")
        ),
        sa.CheckConstraint(
            "text IS NULL OR length(trim(text)) > 0",
            name=op.f("homework_submission_text_nonempty_check"),
        ),
        sa.ForeignKeyConstraint(
            ["homework_id"],
            ["homework.id"],
            name="homework_submission_homework_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["user.id"],
            name="homework_submission_student_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="homework_submission_pkey"),
        sa.UniqueConstraint(
            "homework_id",
            "version",
            name="homework_submission_homework_id_version_key",
        ),
        sa.UniqueConstraint(
            "homework_id",
            "operation_key",
            name="homework_submission_homework_id_operation_key_key",
        ),
    )
    op.create_index(
        "homework_submission_student_created_idx",
        "homework_submission",
        ["student_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "homework_attachment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("homework_id", sa.Uuid(), nullable=True),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("file_name", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("staged_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cleanup_pending",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "byte_size >= 0 AND byte_size <= 20971520",
            name=op.f("homework_attachment_byte_size_within_limit_check"),
        ),
        sa.CheckConstraint(
            "length(file_name) <= 128",
            name=op.f("homework_attachment_file_name_within_limit_check"),
        ),
        sa.CheckConstraint(
            "(homework_id IS NULL AND submission_id IS NULL "
            "AND staged_until IS NOT NULL) OR "
            "(homework_id IS NOT NULL AND staged_until IS NULL)",
            name=op.f("homework_attachment_staging_target_consistent_check"),
        ),
        sa.CheckConstraint(
            "submission_id IS NULL OR homework_id IS NOT NULL",
            name=op.f("homework_attachment_submission_requires_homework_check"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user.id"],
            name="homework_attachment_owner_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["homework_id"],
            ["homework.id"],
            name="homework_attachment_homework_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["homework_submission.id"],
            name="homework_attachment_submission_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="homework_attachment_pkey"),
        sa.UniqueConstraint("storage_key", name="homework_attachment_storage_key_key"),
    )
    op.create_index(
        "homework_attachment_homework_idx",
        "homework_attachment",
        ["homework_id"],
        unique=False,
    )
    op.create_index(
        "homework_attachment_submission_idx",
        "homework_attachment",
        ["submission_id"],
        unique=False,
    )
    op.create_index(
        "homework_attachment_owner_staged_idx",
        "homework_attachment",
        ["owner_user_id", "staged_until"],
        unique=False,
    )
    op.create_index(
        "homework_attachment_cleanup_idx",
        "homework_attachment",
        ["cleanup_pending"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    tables = ("homework_attachment", "homework_submission", "homework")
    row_counts = {
        table: connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in tables
    }
    if any(row_counts.values()):
        raise RuntimeError(
            "Cannot downgrade homework metadata while homework, submission, or "
            f"attachment rows exist: {row_counts}. Export or remove these records "
            "and their stored files through the approved cleanup path first; "
            "no schema changes were made."
        )

    op.drop_index("homework_attachment_cleanup_idx", table_name="homework_attachment")
    op.drop_index(
        "homework_attachment_owner_staged_idx", table_name="homework_attachment"
    )
    op.drop_index(
        "homework_attachment_submission_idx", table_name="homework_attachment"
    )
    op.drop_index("homework_attachment_homework_idx", table_name="homework_attachment")
    op.drop_table("homework_attachment")
    op.drop_index(
        "homework_submission_student_created_idx", table_name="homework_submission"
    )
    op.drop_table("homework_submission")
    op.drop_index("homework_student_created_idx", table_name="homework")
    op.drop_index("homework_teacher_created_idx", table_name="homework")
    op.drop_table("homework")
