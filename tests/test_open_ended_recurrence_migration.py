from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

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

BREAK_MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026-10-29_open_ended_weekly_breaks.py"
)
BREAK_SPEC = spec_from_file_location("open_ended_weekly_breaks", BREAK_MIGRATION_PATH)
assert BREAK_SPEC is not None and BREAK_SPEC.loader is not None
BREAK_MIGRATION = module_from_spec(BREAK_SPEC)
BREAK_SPEC.loader.exec_module(BREAK_MIGRATION)


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


def _run_migration(
    connection: sa.Connection,
    operation: str,
    migration: ModuleType = MIGRATION,
) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        getattr(migration, operation)()


def _insert_recurrent_event(
    connection: sa.Connection,
    *,
    event_id: str,
    event_type: str,
    interval: int,
    interval_end: str | None,
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO recurrent_event "
            '(id, event_type, start, "end", "interval", interval_end, '
            "recurrence_mode, recurrence_timezone, post_break_minutes) "
            "VALUES (:id, :event_type, '2026-10-27 09:00:00', "
            "'2026-10-27 10:00:00', :interval, :interval_end, "
            "'TEACHER_LOCAL', 'UTC', 0)"
        ),
        {
            "id": event_id,
            "event_type": event_type,
            "interval": interval,
            "interval_end": interval_end,
        },
    )


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


def test_weekly_break_migration_preserves_rows_and_refuses_downgrade_with_breaks(
    tmp_path: Path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'weekly-break-migration.db'}")
    try:
        with engine.begin() as connection:
            _create_finite_schema(connection)
            _run_migration(connection, "upgrade")
            _insert_recurrent_event(
                connection,
                event_id="open-lesson",
                event_type="LESSON",
                interval=7,
                interval_end=None,
            )
            _insert_recurrent_event(
                connection,
                event_id="finite-break",
                event_type="BREAK",
                interval=7,
                interval_end="2026-12-01 09:00:00",
            )
            _run_migration(connection, "upgrade", BREAK_MIGRATION)
            _insert_recurrent_event(
                connection,
                event_id="open-break",
                event_type="BREAK",
                interval=7,
                interval_end=None,
            )

            with pytest.raises(IntegrityError):
                _insert_recurrent_event(
                    connection,
                    event_id="nonweekly-open-break",
                    event_type="BREAK",
                    interval=1,
                    interval_end=None,
                )
            with pytest.raises(IntegrityError):
                _insert_recurrent_event(
                    connection,
                    event_id="open-vacation",
                    event_type="VACATION",
                    interval=7,
                    interval_end=None,
                )

        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="no data was modified"):
                _run_migration(connection, "downgrade", BREAK_MIGRATION)

            rows = connection.execute(
                sa.text(
                    "SELECT id, interval_end FROM recurrent_event "
                    "WHERE id IN ('finite', 'finite-break', 'open-lesson', 'open-break') "
                    "ORDER BY id"
                )
            ).all()
            assert rows == [
                ("finite", "2026-12-01 09:00:00"),
                ("finite-break", "2026-12-01 09:00:00"),
                ("open-break", None),
                ("open-lesson", None),
            ]
            interval_end = next(
                column
                for column in inspect(connection).get_columns("recurrent_event")
                if column["name"] == "interval_end"
            )
            assert interval_end["nullable"] is True
            check = next(
                constraint
                for constraint in inspect(connection).get_check_constraints(
                    "recurrent_event"
                )
                if constraint["name"]
                == "recurrent_event_interval_end_after_start_check"
            )
            assert "BREAK" in check["sqltext"]

        with engine.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM recurrent_event WHERE id = 'open-break'")
            )
            _run_migration(connection, "downgrade", BREAK_MIGRATION)
            assert (
                connection.execute(
                    sa.text(
                        "SELECT interval_end FROM recurrent_event "
                        "WHERE id = 'open-lesson'"
                    )
                ).scalar_one()
                is None
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT interval_end FROM recurrent_event "
                        "WHERE id = 'finite-break'"
                    )
                ).scalar_one()
                == "2026-12-01 09:00:00"
            )
            interval_end = next(
                column
                for column in inspect(connection).get_columns("recurrent_event")
                if column["name"] == "interval_end"
            )
            assert interval_end["nullable"] is True
            with pytest.raises(IntegrityError):
                _insert_recurrent_event(
                    connection,
                    event_id="break-after-downgrade",
                    event_type="BREAK",
                    interval=7,
                    interval_end=None,
                )
            _insert_recurrent_event(
                connection,
                event_id="lesson-after-downgrade",
                event_type="LESSON",
                interval=7,
                interval_end=None,
            )
    finally:
        engine.dispose()
