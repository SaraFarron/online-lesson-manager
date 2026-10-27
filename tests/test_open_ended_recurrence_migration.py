from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026-10-27_open_ended_weekly_lessons.py"
)
SPEC = spec_from_file_location("open_ended_weekly_lessons", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def _create_finite_schema(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE recurrent_event (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            event_type VARCHAR NOT NULL,
            start DATETIME NOT NULL,
            "end" DATETIME NOT NULL,
            "interval" INTEGER NOT NULL,
            interval_end DATETIME NOT NULL,
            recurrence_mode VARCHAR(16) NOT NULL,
            recurrence_timezone VARCHAR,
            post_break_minutes INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT recurrent_event_interval_positive_check
                CHECK ("interval" > 0),
            CONSTRAINT recurrent_event_start_before_end_check
                CHECK ("end" > start),
            CONSTRAINT recurrent_event_interval_end_after_start_check
                CHECK (interval_end > start),
            CONSTRAINT recurrent_event_recurrence_mode_valid_check
                CHECK (recurrence_mode IN ('UTC_STEP', 'TEACHER_LOCAL')),
            CONSTRAINT recurrent_event_recurrence_timezone_consistent_check
                CHECK (
                    (recurrence_mode = 'UTC_STEP' AND recurrence_timezone IS NULL)
                    OR (recurrence_mode = 'TEACHER_LOCAL'
                        AND recurrence_timezone IS NOT NULL
                        AND length(trim(recurrence_timezone)) > 0)
                ),
            CONSTRAINT recurrent_event_post_break_minutes_nonnegative_check
                CHECK (post_break_minutes >= 0),
            CONSTRAINT recurrent_event_post_break_minutes_lessons_only_check
                CHECK (event_type = 'LESSON' OR post_break_minutes = 0)
        )
        """
    )
    connection.execute(
        sa.text(
            "INSERT INTO recurrent_event "
            '(id, event_type, start, "end", "interval", interval_end, '
            "recurrence_mode, recurrence_timezone, post_break_minutes) "
            "VALUES ('finite', 'LESSON', :start, :end, 7, :cutoff, "
            "'TEACHER_LOCAL', 'UTC', 0)"
        ),
        {
            "start": datetime(2026, 10, 27, 9),
            "end": datetime(2026, 10, 27, 10),
            "cutoff": datetime(2026, 12, 1, 9),
        },
    )


def _run_migration(connection: sa.Connection, operation: str) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        getattr(MIGRATION, operation)()


def test_migration_preserves_finite_rows_and_refuses_data_loss_on_downgrade(
    tmp_path: Path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        with engine.begin() as connection:
            _create_finite_schema(connection)
            _run_migration(connection, "upgrade")
            finite = connection.execute(
                sa.text("SELECT interval_end FROM recurrent_event WHERE id = 'finite'")
            ).scalar_one()
            assert datetime.fromisoformat(finite) == datetime(2026, 12, 1, 9)

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO recurrent_event "
                    '(id, event_type, start, "end", "interval", interval_end, '
                    "recurrence_mode, recurrence_timezone, post_break_minutes) "
                    "VALUES ('open', 'LESSON', '2026-10-27 09:00:00', "
                    "'2026-10-27 10:00:00', 7, NULL, 'TEACHER_LOCAL', 'UTC', 0)"
                )
            )

        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="no data was modified"):
                _run_migration(connection, "downgrade")
            assert (
                connection.execute(
                    sa.text(
                        "SELECT COUNT(*) FROM recurrent_event WHERE interval_end IS NULL"
                    )
                ).scalar_one()
                == 1
            )
            interval_end = next(
                column
                for column in inspect(connection).get_columns("recurrent_event")
                if column["name"] == "interval_end"
            )
            assert interval_end["nullable"] is True

        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                connection.execute(
                    sa.text(
                        "INSERT INTO recurrent_event "
                        '(id, event_type, start, "end", "interval", interval_end, '
                        "recurrence_mode, recurrence_timezone, post_break_minutes) "
                        "VALUES ('invalid', 'BREAK', '2026-10-27 09:00:00', "
                        "'2026-10-27 10:00:00', 7, NULL, 'TEACHER_LOCAL', 'UTC', 0)"
                    )
                )

        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM recurrent_event WHERE id = 'open'"))
            _run_migration(connection, "downgrade")
            assert (
                connection.execute(
                    sa.text(
                        "SELECT interval_end FROM recurrent_event WHERE id = 'finite'"
                    )
                ).scalar_one()
                == "2026-12-01 09:00:00"
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    sa.text(
                        "INSERT INTO recurrent_event "
                        '(id, event_type, start, "end", "interval", interval_end, '
                        "recurrence_mode, recurrence_timezone, post_break_minutes) "
                        "VALUES ('null-after-downgrade', 'LESSON', "
                        "'2026-10-27 09:00:00', '2026-10-27 10:00:00', 7, NULL, "
                        "'TEACHER_LOCAL', 'UTC', 0)"
                    )
                )
    finally:
        engine.dispose()
