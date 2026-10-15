"""Add recurrence mode metadata and integrity constraints.

Revision ID: 6b87556d4a21
Revises: 8bb41e3a1002
"""

import sqlalchemy as sa

from alembic import op

revision = "6b87556d4a21"
down_revision = "8bb41e3a1002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    invalid_series = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM recurrent_event "
            'WHERE "interval" <= 0 OR "start" >= "end" OR "interval_end" <= "start"'
        )
    ).scalar_one()
    duplicate_cancellations = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            'SELECT recurrent_event_id, "start" FROM recurrent_cancels '
            'GROUP BY recurrent_event_id, "start" HAVING COUNT(*) > 1'
            ")"
        )
    ).scalar_one()
    if invalid_series or duplicate_cancellations:
        raise RuntimeError(
            "Cannot add recurrence integrity constraints: "
            f"{invalid_series} invalid series and "
            f"{duplicate_cancellations} duplicate cancellation starts. "
            "Remediate these rows explicitly and retry; no scheduling rows "
            "were modified."
        )

    with op.batch_alter_table("recurrent_event") as batch:
        batch.add_column(
            sa.Column(
                "recurrence_mode",
                sa.String(length=16),
                nullable=False,
                server_default="UTC_STEP",
            )
        )
        batch.add_column(sa.Column("recurrence_timezone", sa.String(), nullable=True))
        batch.create_check_constraint(
            batch.f("recurrent_event_interval_positive_check"), "interval > 0"
        )
        batch.create_check_constraint(
            batch.f("recurrent_event_start_before_end_check"), '"end" > start'
        )
        batch.create_check_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"),
            "interval_end > start",
        )
        batch.create_check_constraint(
            batch.f("recurrent_event_recurrence_mode_valid_check"),
            "recurrence_mode IN ('UTC_STEP', 'TEACHER_LOCAL')",
        )
        batch.create_check_constraint(
            batch.f("recurrent_event_recurrence_timezone_consistent_check"),
            "(recurrence_mode = 'UTC_STEP' AND recurrence_timezone IS NULL) OR "
            "(recurrence_mode = 'TEACHER_LOCAL' AND recurrence_timezone IS NOT NULL "
            "AND length(trim(recurrence_timezone)) > 0)",
        )

    with op.batch_alter_table("recurrent_cancels") as batch:
        batch.create_unique_constraint(
            "recurrent_cancels_recurrent_event_id_start_key",
            ["recurrent_event_id", "start"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    local_series = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM recurrent_event "
            "WHERE recurrence_mode = 'TEACHER_LOCAL'"
        )
    ).scalar_one()
    if local_series:
        raise RuntimeError(
            "Cannot downgrade recurrence metadata while teacher-local series exist. "
            "Convert or remove those series explicitly before retrying; no data "
            "was modified."
        )

    with op.batch_alter_table("recurrent_cancels") as batch:
        batch.drop_constraint(
            batch.f("recurrent_cancels_recurrent_event_id_start_key"),
            type_="unique",
        )

    with op.batch_alter_table("recurrent_event") as batch:
        batch.drop_constraint(
            batch.f("recurrent_event_recurrence_timezone_consistent_check"),
            type_="check",
        )
        batch.drop_constraint(
            batch.f("recurrent_event_recurrence_mode_valid_check"), type_="check"
        )
        batch.drop_constraint(
            batch.f("recurrent_event_interval_end_after_start_check"), type_="check"
        )
        batch.drop_constraint(
            batch.f("recurrent_event_start_before_end_check"), type_="check"
        )
        batch.drop_constraint(
            batch.f("recurrent_event_interval_positive_check"), type_="check"
        )
        batch.drop_column("recurrence_timezone")
        batch.drop_column("recurrence_mode")
