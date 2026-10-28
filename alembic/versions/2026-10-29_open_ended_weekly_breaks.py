"""Allow weekly breaks to have no recurrence cutoff.

Revision ID: c4e6a8f19d23
Revises: 0f2c9b73e641
"""

import sqlalchemy as sa

from alembic import op

revision = "c4e6a8f19d23"
down_revision = "0f2c9b73e641"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recurrent_event") as batch:
        batch.drop_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            type_="check",
        )
        batch.create_check_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            "(interval_end IS NOT NULL AND interval_end > start) OR "
            "(interval_end IS NULL AND event_type IN ('LESSON', 'BREAK') "
            "AND \"interval\" = 7)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    open_ended_breaks = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM recurrent_event "
            "WHERE interval_end IS NULL AND event_type = 'BREAK'"
        )
    ).scalar_one()
    if open_ended_breaks:
        raise RuntimeError(
            "Cannot downgrade while open-ended weekly breaks exist. Set an "
            "explicit finite cutoff for each break before retrying; no data "
            "was modified."
        )

    with op.batch_alter_table("recurrent_event") as batch:
        batch.drop_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            type_="check",
        )
        batch.create_check_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            "(interval_end IS NOT NULL AND interval_end > start) OR "
            "(interval_end IS NULL AND event_type = 'LESSON' AND \"interval\" = 7)",
        )
