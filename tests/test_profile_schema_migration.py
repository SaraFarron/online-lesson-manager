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
    / "2026-11-03_teacher_student_profiles.py"
)
SPEC = spec_from_file_location("teacher_student_profiles_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def _run_migration(connection: sa.Connection, operation: str) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        getattr(MIGRATION, operation)()


def _create_legacy_schema(connection: sa.Connection) -> tuple[str, str]:
    connection.exec_driver_sql(
        'CREATE TABLE "user" (id CHAR(32) PRIMARY KEY, teacher_id CHAR(32))'
    )
    teacher_id = "00000000000000000000000000000001"
    student_id = "00000000000000000000000000000002"
    connection.execute(
        sa.text(
            'INSERT INTO "user" (id, teacher_id) VALUES (:id, NULL), (:student, :teacher)'
        ),
        {"id": teacher_id, "student": student_id, "teacher": teacher_id},
    )
    return teacher_id, student_id


def test_migration_backfills_unknown_link_dates_and_round_trips() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            teacher_id, student_id = _create_legacy_schema(connection)
            _run_migration(connection, "upgrade")

            link = connection.execute(
                sa.text(
                    "SELECT id, student_id, teacher_id, linked_at, removed_at "
                    "FROM student_teacher_relationship"
                )
            ).one()
            assert link.id == student_id
            assert link.student_id == student_id
            assert link.teacher_id == teacher_id
            assert link.linked_at is None
            assert link.removed_at is None
            assert "telegram_username" in {
                column["name"] for column in sa.inspect(connection).get_columns("user")
            }
            assert (
                connection.execute(
                    sa.text("SELECT COUNT(*) FROM student_action")
                ).scalar_one()
                == 0
            )

            _run_migration(connection, "downgrade")
            assert (
                "student_teacher_relationship"
                not in sa.inspect(connection).get_table_names()
            )
            assert "student_action" not in sa.inspect(connection).get_table_names()
            assert (
                connection.execute(
                    sa.text('SELECT teacher_id FROM "user" WHERE id = :student'),
                    {"student": student_id},
                ).scalar_one()
                == teacher_id
            )
            assert "telegram_username" not in {
                column["name"] for column in sa.inspect(connection).get_columns("user")
            }
    finally:
        engine.dispose()


def test_downgrade_refuses_to_discard_new_profile_data() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            teacher_id, student_id = _create_legacy_schema(connection)
            _run_migration(connection, "upgrade")
            connection.execute(
                sa.text(
                    'UPDATE "user" SET telegram_username = :username WHERE id = :id'
                ),
                {"username": "Student_1", "id": student_id},
            )
            link_id = connection.execute(
                sa.text(
                    "SELECT id FROM student_teacher_relationship "
                    "WHERE student_id = :student"
                ),
                {"student": student_id},
            ).scalar_one()
            connection.execute(
                sa.text(
                    "INSERT INTO student_action "
                    "(id, relationship_id, teacher_id, student_id, actor_id, "
                    "action_type, resource_type, resource_id, "
                    "resource_occurrence_at, occurred_at) "
                    "VALUES (:id, :relationship_id, :teacher_id, :student_id, "
                    ":actor_id, 'homework_submitted', NULL, NULL, NULL, "
                    "'2026-11-03 00:00:00')"
                ),
                {
                    "id": "00000000000000000000000000000003",
                    "relationship_id": link_id,
                    "teacher_id": teacher_id,
                    "student_id": student_id,
                    "actor_id": student_id,
                },
            )

            with pytest.raises(RuntimeError, match="no data was modified"):
                _run_migration(connection, "downgrade")

            assert "student_action" in sa.inspect(connection).get_table_names()
            assert (
                connection.execute(
                    sa.text("SELECT COUNT(*) FROM student_action")
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    sa.text('SELECT telegram_username FROM "user" WHERE id = :id'),
                    {"id": student_id},
                ).scalar_one()
                == "Student_1"
            )
    finally:
        engine.dispose()
