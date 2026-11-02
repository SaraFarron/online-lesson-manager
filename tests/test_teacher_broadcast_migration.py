from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026-11-02_teacher_broadcasts.py"
)
SPEC = spec_from_file_location("teacher_broadcast_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def _run_migration(connection: sa.Connection, operation: str) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        getattr(MIGRATION, operation)()


def test_teacher_broadcast_migration_round_trips_and_guards_saved_data() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE user (id CHAR(32) PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE notification_intent ("
                "id CHAR(32) PRIMARY KEY, "
                "notification_type VARCHAR(32) NOT NULL, "
                "CONSTRAINT notification_intent_notification_type_valid_check "
                "CHECK (notification_type IN "
                "('DAILY_DIGEST', 'LESSON_REMINDER', 'HOMEWORK_REMINDER', "
                "'ASSIGNMENT_CREATED', 'SUBMISSION_RECEIVED')))"
            )

            _run_migration(connection, "upgrade")
            assert "teacher_broadcast" in sa.inspect(connection).get_table_names()
            connection.exec_driver_sql(
                "INSERT INTO user VALUES ('00000000000000000000000000000001')"
            )
            connection.exec_driver_sql(
                "INSERT INTO teacher_broadcast "
                "(id, teacher_id, operation_key, confirmation_token, message, "
                "recipient_count, eligible_count, unsendable_count, created_at, "
                "updated_at) VALUES "
                "('00000000000000000000000000000002', "
                "'00000000000000000000000000000001', "
                "'00000000000000000000000000000003', 'token', 'Message', 0, 0, 0, "
                "'2026-11-02', '2026-11-02')"
            )
            with pytest.raises(RuntimeError, match="no data was modified"):
                _run_migration(connection, "downgrade")
            assert "teacher_broadcast" in sa.inspect(connection).get_table_names()

            connection.exec_driver_sql("DELETE FROM teacher_broadcast")
            connection.exec_driver_sql(
                "INSERT INTO notification_intent (id, notification_type) "
                "VALUES ('00000000000000000000000000000004', 'TEACHER_BROADCAST')"
            )
            with pytest.raises(RuntimeError, match="no data was modified"):
                _run_migration(connection, "downgrade")
            assert "teacher_broadcast" in sa.inspect(connection).get_table_names()

            connection.exec_driver_sql("DELETE FROM notification_intent")
            _run_migration(connection, "downgrade")
            assert "teacher_broadcast" not in sa.inspect(connection).get_table_names()
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO notification_intent (id, notification_type) "
                    "VALUES ('intent', 'TEACHER_BROADCAST')"
                )
    finally:
        engine.dispose()
