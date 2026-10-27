"""Allow weekly lessons to have no recurrence cutoff.

Revision ID: 0f2c9b73e641
Revises: d2f0409b6e21
"""

import sqlalchemy as sa

from alembic import op

revision = "0f2c9b73e641"
down_revision = "d2f0409b6e21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recurrent_event") as batch:
        batch.drop_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            type_="check",
        )
        batch.alter_column(
            "interval_end",
            existing_type=sa.DateTime(),
            nullable=True,
        )
        batch.create_check_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            "(interval_end IS NOT NULL AND interval_end > start) OR "
            "(interval_end IS NULL AND event_type = 'LESSON' AND \"interval\" = 7)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    open_ended_series = connection.execute(
        sa.text("SELECT COUNT(*) FROM recurrent_event WHERE interval_end IS NULL")
    ).scalar_one()
    if open_ended_series:
        raise RuntimeError(
            "Cannot downgrade while open-ended weekly lessons exist. Set an "
            "explicit finite cutoff for each series before retrying; no data "
            "was modified."
        )

    with op.batch_alter_table("recurrent_event") as batch:
        batch.drop_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            type_="check",
        )
        batch.alter_column(
            "interval_end",
            existing_type=sa.DateTime(),
            nullable=False,
        )
        batch.create_check_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            "interval_end > start",
        )
