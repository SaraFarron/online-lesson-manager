import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.activity import service as activity_service
from backend.activity.constants import StudentActionType
from backend.activity.models import StudentAction
from backend.auth import service as auth_service
from backend.auth.models import (
    StudentProfile,
    StudentTeacherRelationship,
    TeacherProfile,
    User,
)
from backend.auth.utils import create_access_token


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _create_linked_users(
    session: AsyncSession,
) -> tuple[User, User, StudentTeacherRelationship]:
    teacher = User(role="TEACHER", timezone="UTC", full_name="Teacher")
    student = User(
        email="linked-student@example.com",
        role="STUDENT",
        timezone="UTC",
        full_name="Student",
    )
    session.add_all([teacher, student])
    await session.flush()
    session.add(TeacherProfile(teacher_id=teacher.id, code="AUTHHISTORY"))
    await session.commit()
    await auth_service.link_student_to_teacher(session, student, "AUTHHISTORY")
    relationship = await session.scalar(
        select(StudentTeacherRelationship).where(
            StudentTeacherRelationship.student_id == student.id
        )
    )
    assert relationship is not None
    return teacher, student, relationship


async def _actions(session: AsyncSession, student_id: uuid.UUID) -> list[StudentAction]:
    result = await session.scalars(
        select(StudentAction)
        .where(StudentAction.student_id == student_id)
        .order_by(StudentAction.occurred_at, StudentAction.id)
    )
    return list(result)


async def test_auth_actions_are_scoped_to_student_changes_and_ignore_replays(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher, student, relationship = await _create_linked_users(db_session)
    initial_actions = await _actions(db_session, student.id)
    assert [action.action_type for action in initial_actions] == [
        StudentActionType.TEACHER_LINKED
    ]

    headers = _headers(student)
    profile_url = f"/api/v1/auth/users/{student.id}/student-profile"
    created = await client.post(profile_url, json={}, headers=headers)
    assert created.status_code == 201, created.text

    updated = await client.patch(
        "/api/v1/auth/me",
        json={
            "full_name": "Updated Student",
            "timezone": "Europe/London",
            "notification_lessons_today": 600,
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    settings_updated = await client.put(
        profile_url,
        json={"notification_lesson": None, "notification_homework": 4},
        headers=headers,
    )
    assert settings_updated.status_code == 200, settings_updated.text

    action_types = [
        action.action_type for action in await _actions(db_session, student.id)
    ]
    assert action_types == [
        StudentActionType.TEACHER_LINKED,
        StudentActionType.STUDENT_PROFILE_CREATED,
        StudentActionType.ACCOUNT_PROFILE_UPDATED,
        StudentActionType.STUDENT_SETTINGS_UPDATED,
    ]
    for action in await _actions(db_session, student.id):
        assert action.actor_id == student.id
        assert action.teacher_id == teacher.id
        assert action.relationship_id == relationship.id
        assert action.resource_type is None
        assert action.resource_id is None

    replayed_profile = await client.patch(
        "/api/v1/auth/me",
        json={
            "full_name": "Updated Student",
            "timezone": "Europe/London",
            "notification_lessons_today": 600,
        },
        headers=headers,
    )
    replayed_settings = await client.put(
        profile_url,
        json={"notification_lesson": None, "notification_homework": 4},
        headers=headers,
    )
    read = await client.get("/api/v1/auth/me", headers=headers)
    assert replayed_profile.status_code == replayed_settings.status_code == 200
    assert read.status_code == 200
    assert len(await _actions(db_session, student.id)) == 4

    duplicate_link = await client.post(
        "/api/v1/auth/me/teacher",
        json={"teacher_code": "AUTHHISTORY"},
        headers=headers,
    )
    assert duplicate_link.status_code == 400
    assert len(await _actions(db_session, student.id)) == 4


async def test_unlinked_student_and_teacher_mutations_are_not_recorded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher, student, _ = await _create_linked_users(db_session)
    unlinked = User(
        email="unlinked-student@example.com",
        role="STUDENT",
        timezone="UTC",
    )
    db_session.add(unlinked)
    await db_session.commit()

    unlinked_headers = _headers(unlinked)
    unlinked_url = f"/api/v1/auth/users/{unlinked.id}/student-profile"
    created = await client.post(unlinked_url, json={}, headers=unlinked_headers)
    updated = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Unlinked"},
        headers=unlinked_headers,
    )
    settings = await client.put(
        unlinked_url, json={"notification_homework": 5}, headers=unlinked_headers
    )
    assert created.status_code == 201
    assert updated.status_code == settings.status_code == 200
    assert await _actions(db_session, unlinked.id) == []

    teacher_update = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Updated Teacher"},
        headers=_headers(teacher),
    )
    assert teacher_update.status_code == 200
    assert [
        action.action_type for action in await _actions(db_session, student.id)
    ] == [StudentActionType.TEACHER_LINKED]

    admin = User(
        email="profile-admin@example.com",
        role="ADMIN",
        timezone="UTC",
        is_superuser=True,
    )
    db_session.add(admin)
    await db_session.commit()
    profile_url = f"/api/v1/auth/users/{student.id}/student-profile"
    admin_created = await client.post(profile_url, json={}, headers=_headers(admin))
    assert admin_created.status_code == 201
    admin_update = await client.put(
        profile_url,
        json={"notification_homework": 6},
        headers=_headers(admin),
    )
    assert admin_update.status_code == 200
    assert [
        action.action_type for action in await _actions(db_session, student.id)
    ] == [StudentActionType.TEACHER_LINKED]


async def test_student_action_flush_uses_caller_transaction(
    db_session: AsyncSession,
) -> None:
    _, student, relationship = await _create_linked_users(db_session)
    student_id = student.id
    before = len(await _actions(db_session, student_id))

    action = await activity_service.record_student_action(
        db_session,
        student,
        StudentActionType.ACCOUNT_PROFILE_UPDATED,
        relationship=relationship,
        occurred_at=datetime(2026, 11, 3, tzinfo=UTC),
    )
    assert action is not None
    await db_session.rollback()
    assert len(await _actions(db_session, student_id)) == before


async def test_telegram_registration_records_account_and_link_once(
    client: AsyncClient, db_session: AsyncSession, bot_service_key: str
) -> None:
    teacher = User(role="TEACHER", timezone="UTC")
    db_session.add(teacher)
    await db_session.flush()
    db_session.add(TeacherProfile(teacher_id=teacher.id, code="REGHISTORY"))
    await db_session.commit()
    headers = {"X-Bot-Service-Key": bot_service_key}
    payload = {
        "telegram_user_id": 9_000_000_011,
        "telegram_private_chat_id": 9_000_000_011,
        "role": "STUDENT",
        "timezone": "UTC",
        "teacher_code": "REGHISTORY",
    }

    registered = await client.post(
        "/api/v1/telegram/register", headers=headers, json=payload
    )
    assert registered.status_code == 201, registered.text
    student = await db_session.scalar(
        select(User).where(User.telegram_user_id == 9_000_000_011)
    )
    assert student is not None
    action_types = [
        action.action_type for action in await _actions(db_session, student.id)
    ]
    assert action_types == [
        StudentActionType.ACCOUNT_REGISTERED,
        StudentActionType.TEACHER_LINKED,
    ]
    assert await db_session.scalar(
        select(StudentProfile).where(StudentProfile.student_id == student.id)
    )

    duplicate = await client.post(
        "/api/v1/telegram/register", headers=headers, json=payload
    )
    assert duplicate.status_code == 409
    assert len(await _actions(db_session, student.id)) == 2
