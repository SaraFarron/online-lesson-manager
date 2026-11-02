import uuid
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, User
from backend.notifications.models import NotificationIntent

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026-11-01_notification_delivery_schema.py"
)
SPEC = spec_from_file_location("notification_delivery_schema", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)
SCHEDULE_MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026-11-02_notification_schedule_timestamps.py"
)
SCHEDULE_SPEC = spec_from_file_location(
    "notification_schedule_timestamps", SCHEDULE_MIGRATION_PATH
)
assert SCHEDULE_SPEC is not None and SCHEDULE_SPEC.loader is not None
SCHEDULE_MIGRATION = module_from_spec(SCHEDULE_SPEC)
SCHEDULE_SPEC.loader.exec_module(SCHEDULE_MIGRATION)


async def _register_and_login(
    client: AsyncClient, email: str, role: str
) -> tuple[str, str]:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "timezone": "UTC",
        },
    )
    assert register.status_code == 201, register.text
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert login.status_code == 200, login.text
    return register.json()["id"], login.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_homework_push_preferences_default_and_update_independently(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, teacher_token = await _register_and_login(
        client, "notification-teacher@example.com", "TEACHER"
    )
    teacher_headers = _auth_header(teacher_token)
    teacher_profile_url = f"/api/v1/auth/users/{teacher_id}/teacher-profile"
    teacher_created = await client.post(
        teacher_profile_url,
        json={"code": "NOTIFYTEACHER"},
        headers=teacher_headers,
    )
    assert teacher_created.status_code == 201, teacher_created.text
    assert teacher_created.json()["notification_homework_submission"] is True
    teacher_user = await db_session.get(User, uuid.UUID(teacher_id))
    assert teacher_user is not None
    initial_schedule_timestamp = teacher_user.notification_schedule_updated_at
    digest_update = await client.patch(
        "/api/v1/auth/me",
        json={"notification_lessons_today": 600},
        headers=teacher_headers,
    )
    assert digest_update.status_code == 200, digest_update.text
    await db_session.refresh(teacher_user)
    assert teacher_user.notification_schedule_updated_at > initial_schedule_timestamp

    student_id, student_token = await _register_and_login(
        client, "notification-student@example.com", "STUDENT"
    )
    student_headers = _auth_header(student_token)
    student_profile_url = f"/api/v1/auth/users/{student_id}/student-profile"
    student_created = await client.post(
        student_profile_url,
        json={},
        headers=student_headers,
    )
    assert student_created.status_code == 201, student_created.text
    assert student_created.json()["notification_homework_assignment"] is True
    student_profile = await db_session.scalar(
        select(StudentProfile).where(StudentProfile.student_id == uuid.UUID(student_id))
    )
    assert student_profile is not None
    initial_reminder_timestamp = student_profile.notification_lesson_updated_at

    teacher_disabled = await client.put(
        teacher_profile_url,
        json={"notification_homework_submission": False},
        headers=teacher_headers,
    )
    student_disabled = await client.put(
        student_profile_url,
        json={"notification_homework_assignment": False},
        headers=student_headers,
    )
    assert teacher_disabled.status_code == 200, teacher_disabled.text
    assert student_disabled.status_code == 200, student_disabled.text

    student_other_setting = await client.put(
        student_profile_url,
        json={"notification_lesson": None},
        headers=student_headers,
    )
    assert student_other_setting.status_code == 200, student_other_setting.text
    assert student_other_setting.json()["notification_homework_assignment"] is False
    await db_session.refresh(student_profile)
    assert student_profile.notification_lesson_updated_at > initial_reminder_timestamp

    _, other_teacher_token = await _register_and_login(
        client, "other-notification-teacher@example.com", "TEACHER"
    )
    unauthorized = await client.put(
        teacher_profile_url,
        json={"notification_homework_submission": True},
        headers=_auth_header(other_teacher_token),
    )
    assert unauthorized.status_code == 403, unauthorized.text

    null_toggle = await client.put(
        student_profile_url,
        json={"notification_homework_assignment": None},
        headers=student_headers,
    )
    assert null_toggle.status_code == 422, null_toggle.text


async def test_notification_intent_dedupe_key_is_unique(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    recipient = User(
        email="intent-recipient@example.com",
        role="STUDENT",
        timezone="UTC",
    )
    db_session.add(recipient)
    await db_session.flush()

    def make_intent() -> NotificationIntent:
        return NotificationIntent(
            recipient_id=recipient.id,
            notification_type="DAILY_DIGEST",
            dedupe_key="recipient:digest:2026-11-01",
            source_date=now.date(),
            scheduled_at=now,
            available_at=now,
            expires_at=now + timedelta(hours=12),
        )

    db_session.add(make_intent())
    await db_session.flush()
    db_session.add(make_intent())
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


def _run_migration(
    connection: sa.Connection,
    operation: str,
    migration: object = MIGRATION,
) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        getattr(migration, operation)()


def test_notification_schedule_timestamp_migration_round_trips() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE user (id CHAR(32) PRIMARY KEY, "
                "notification_lessons_today INTEGER)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE student_profile (id CHAR(32) PRIMARY KEY, "
                "notification_lesson INTEGER)"
            )
            connection.exec_driver_sql("INSERT INTO user VALUES ('user-1', 540)")
            connection.exec_driver_sql(
                "INSERT INTO student_profile VALUES ('student-1', 15)"
            )

            _run_migration(connection, "upgrade", SCHEDULE_MIGRATION)

            user_timestamp = connection.exec_driver_sql(
                "SELECT notification_schedule_updated_at FROM user WHERE id = 'user-1'"
            ).scalar_one()
            student_timestamp = connection.exec_driver_sql(
                "SELECT notification_lesson_updated_at FROM student_profile "
                "WHERE id = 'student-1'"
            ).scalar_one()
            assert user_timestamp is not None
            assert student_timestamp is not None

            _run_migration(connection, "downgrade", SCHEDULE_MIGRATION)
            assert "notification_schedule_updated_at" not in {
                column["name"] for column in sa.inspect(connection).get_columns("user")
            }
            assert "notification_lesson_updated_at" not in {
                column["name"]
                for column in sa.inspect(connection).get_columns("student_profile")
            }
    finally:
        engine.dispose()


def test_notification_migration_preserves_legacy_rows_and_guards_downgrade() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE user (id CHAR(32) PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE student_profile (id CHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE teacher_profile (id CHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE notification (id CHAR(32), receivers VARCHAR(255), "
                "notification_type VARCHAR(50), message VARCHAR(1024), "
                "is_sent BOOLEAN, created_at DATETIME, updated_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO notification VALUES "
                "('legacy-id', 'legacy receiver', 'legacy', 'keep me', 0, "
                "'2026-11-01', '2026-11-01')"
            )
            connection.exec_driver_sql(
                "INSERT INTO user VALUES ('00000000000000000000000000000001')"
            )
            connection.exec_driver_sql(
                "INSERT INTO student_profile VALUES "
                "('00000000000000000000000000000002')"
            )
            connection.exec_driver_sql(
                "INSERT INTO teacher_profile VALUES "
                "('00000000000000000000000000000003')"
            )
            _run_migration(connection, "upgrade")

            legacy = connection.exec_driver_sql(
                "SELECT receivers, notification_type, message, is_sent "
                "FROM notification WHERE id = 'legacy-id'"
            ).one()
            assert tuple(legacy) == ("legacy receiver", "legacy", "keep me", 0)
            assert (
                connection.exec_driver_sql(
                    "SELECT notification_homework_assignment FROM student_profile"
                ).scalar_one()
                == 1
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT notification_homework_submission FROM teacher_profile"
                ).scalar_one()
                == 1
            )
            indexes = {
                index["name"]
                for index in sa.inspect(connection).get_indexes("notification_intent")
            }
            assert "ix_notification_intent_state_available_at" in indexes

            connection.exec_driver_sql(
                "INSERT INTO user VALUES ('00000000000000000000000000000004')"
            )
            connection.execute(
                sa.text(
                    "INSERT INTO notification_intent "
                    "(id, recipient_id, notification_type, dedupe_key, scheduled_at, "
                    "available_at, expires_at, created_at, updated_at) VALUES "
                    "(:id, :recipient_id, 'DAILY_DIGEST', 'unique-key', "
                    "'2026-11-01', '2026-11-01', '2026-11-02', "
                    "'2026-11-01', '2026-11-01')"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "recipient_id": "00000000000000000000000000000004",
                },
            )
            with pytest.raises(RuntimeError, match="no data was modified"):
                _run_migration(connection, "downgrade")
            assert (
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM notification_intent"
                ).scalar_one()
                == 1
            )

            connection.exec_driver_sql("DELETE FROM notification_intent")
            connection.exec_driver_sql(
                "UPDATE student_profile SET notification_homework_assignment = 0"
            )
            with pytest.raises(RuntimeError, match="no data was modified"):
                _run_migration(connection, "downgrade")
            connection.exec_driver_sql(
                "UPDATE student_profile SET notification_homework_assignment = 1"
            )
            _run_migration(connection, "downgrade")
            assert (
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM notification"
                ).scalar_one()
                == 1
            )
            assert "notification_intent" not in sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()
