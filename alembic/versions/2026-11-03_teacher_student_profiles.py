"""Add teacher/student relationship history and student action storage.

Revision ID: 20261103profiles
Revises: 20261102broadcast
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "20261103profiles"
down_revision = "20261102broadcast"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("telegram_username", sa.String(length=32), nullable=True),
    )

    op.create_table(
        "student_teacher_relationship",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "removed_at IS NULL OR linked_at IS NULL OR removed_at >= linked_at",
            name="removed_after_link",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["user.id"],
            name="student_teacher_relationship_student_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["user.id"],
            name="student_teacher_relationship_teacher_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="student_teacher_relationship_pkey"),
        sa.UniqueConstraint(
            "id",
            "teacher_id",
            "student_id",
            name="relationship_scope",
        ),
    )

    bind = op.get_bind()
    relationship = sa.table(
        "student_teacher_relationship",
        sa.column("id", sa.Uuid()),
        sa.column("student_id", sa.Uuid()),
        sa.column("teacher_id", sa.Uuid()),
        sa.column("linked_at", sa.DateTime(timezone=True)),
        sa.column("removed_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    existing_links = bind.execute(
        sa.text('SELECT id, teacher_id FROM "user" WHERE teacher_id IS NOT NULL')
    )
    recorded_at = datetime.now(UTC)
    for student_id, teacher_id in existing_links:
        bind.execute(
            relationship.insert().values(
                id=uuid.UUID(str(student_id)),
                student_id=uuid.UUID(str(student_id)),
                teacher_id=uuid.UUID(str(teacher_id)),
                linked_at=None,
                removed_at=None,
                created_at=recorded_at,
                updated_at=recorded_at,
            )
        )

    op.create_index(
        "student_teacher_relationship_one_active_per_student_idx",
        "student_teacher_relationship",
        ["student_id"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
        sqlite_where=sa.text("removed_at IS NULL"),
    )
    op.create_index(
        "student_teacher_relationship_teacher_removed_student_idx",
        "student_teacher_relationship",
        ["teacher_id", "removed_at", "student_id"],
    )

    op.create_table(
        "student_action",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("relationship_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("resource_occurrence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_id = student_id",
            name="student_is_actor",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["user.id"],
            name="student_action_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id", "teacher_id", "student_id"],
            [
                "student_teacher_relationship.id",
                "student_teacher_relationship.teacher_id",
                "student_teacher_relationship.student_id",
            ],
            name="student_action_relationship_scope_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="student_action_pkey"),
    )
    op.create_index(
        "student_action_relationship_occurred_id_idx",
        "student_action",
        ["relationship_id", "occurred_at", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    action_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM student_action")
    ).scalar_one()
    relationship_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM student_teacher_relationship "
            "WHERE linked_at IS NOT NULL OR removed_at IS NOT NULL"
        )
    ).scalar_one()
    username_count = bind.execute(
        sa.text('SELECT COUNT(*) FROM "user" WHERE telegram_username IS NOT NULL')
    ).scalar_one()
    if action_count or relationship_count or username_count:
        raise RuntimeError(
            "Cannot downgrade teacher/student profile data while action history, "
            "known relationship dates/removals, or Telegram usernames exist. "
            "Preserve or resolve this data before retrying; no data was modified."
        )

    op.drop_index(
        "student_action_relationship_occurred_id_idx",
        table_name="student_action",
    )
    op.drop_table("student_action")
    op.drop_index(
        "student_teacher_relationship_teacher_removed_student_idx",
        table_name="student_teacher_relationship",
    )
    op.drop_index(
        "student_teacher_relationship_one_active_per_student_idx",
        table_name="student_teacher_relationship",
    )
    op.drop_table("student_teacher_relationship")
    op.drop_column("user", "telegram_username")
