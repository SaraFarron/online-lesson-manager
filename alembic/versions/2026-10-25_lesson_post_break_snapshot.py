"""Add lesson post-break duration snapshots.

Revision ID: a73d0c4b9e21
Revises: c61e7b0a4d29
"""

import sqlalchemy as sa

from alembic import op

revision = "a73d0c4b9e21"
down_revision = "c61e7b0a4d29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("event", "recurrent_event"):
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(
                sa.Column(
                    "post_break_minutes",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )
            batch.create_check_constraint(
                "post_break_minutes_nonnegative", "post_break_minutes >= 0"
            )
            batch.create_check_constraint(
                "post_break_minutes_lessons_only",
                "event_type = 'LESSON' OR post_break_minutes = 0",
            )


def downgrade() -> None:
    connection = op.get_bind()
    nonzero_snapshots = {
        table_name: connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE post_break_minutes != 0")
        ).scalar_one()
        for table_name in ("event", "recurrent_event")
    }
    if any(nonzero_snapshots.values()):
        raise RuntimeError(
            "Cannot downgrade lesson post-break snapshots while nonzero durations "
            f"exist: {nonzero_snapshots}. Preserve or remove affected lesson "
            "snapshots explicitly before retrying; no data was modified."
        )

    for table_name in ("recurrent_event", "event"):
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(
                batch.f(f"{table_name}_post_break_minutes_lessons_only_check"),
                type_="check",
            )
            batch.drop_constraint(
                batch.f(f"{table_name}_post_break_minutes_nonnegative_check"),
                type_="check",
            )
            batch.drop_column("post_break_minutes")
