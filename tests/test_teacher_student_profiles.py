import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from backend.activity.constants import StudentActionType
from backend.activity.models import StudentAction
from backend.auth.models import (
    StudentProfile,
    StudentTeacherRelationship,
    User,
)
from backend.auth.utils import create_access_token
from backend.events.models import Event
from backend.homework.models import Homework


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


async def _teacher(session: AsyncSession, name: str) -> User:
    teacher = User(role="TEACHER", timezone="UTC", full_name=name)
    session.add(teacher)
    await session.flush()
    return teacher


async def _relationship(
    session: AsyncSession,
    teacher: User,
    student: User,
    *,
    linked_at: datetime | None,
    removed_at: datetime | None = None,
) -> StudentTeacherRelationship:
    relationship = StudentTeacherRelationship(
        teacher_id=teacher.id,
        student_id=student.id,
        linked_at=linked_at,
        removed_at=removed_at,
    )
    session.add(relationship)
    await session.flush()
    return relationship


async def test_active_profile_has_minimal_summary_and_latest_ten_actions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher = await _teacher(db_session, "Teacher")
    student = User(
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
        full_name="   ",
        telegram_username="",
        email="private@example.com",
        hashed_password="private-hash",
        telegram_user_id=123456,
        telegram_private_chat_id=654321,
    )
    db_session.add(student)
    await db_session.flush()
    db_session.add(
        StudentProfile(
            student_id=student.id,
            notification_lesson=15,
            notification_homework=2,
            notification_homework_assignment=True,
        )
    )
    relationship = await _relationship(db_session, teacher, student, linked_at=None)

    completed_homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        status="COMPLETE",
        completion_source="TEACHER",
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        operation_key=uuid.uuid4(),
    )
    incomplete_homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        status="INCOMPLETE",
        operation_key=uuid.uuid4(),
    )
    student_completed_homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        status="COMPLETE",
        completion_source="STUDENT_SUBMISSION",
        completed_at=datetime(2026, 1, 2, tzinfo=UTC),
        operation_key=uuid.uuid4(),
    )
    db_session.add_all(
        [completed_homework, incomplete_homework, student_completed_homework]
    )
    await db_session.flush()

    occurred_at = datetime(2026, 2, 1, tzinfo=UTC)
    actions = [
        StudentAction(
            id=uuid.UUID(int=index),
            relationship_id=relationship.id,
            teacher_id=teacher.id,
            student_id=student.id,
            actor_id=student.id,
            action_type=StudentActionType.ACCOUNT_PROFILE_UPDATED,
            occurred_at=occurred_at,
            resource_type=("homework" if index in {11, 12} else None),
            resource_id=(
                completed_homework.id
                if index == 12
                else uuid.uuid4()
                if index == 11
                else None
            ),
        )
        for index in range(1, 13)
    ]
    db_session.add_all(actions)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/auth/teacher/students/profiles/{student.id}",
        headers=_headers(teacher),
    )
    assert response.status_code == 200, response.text
    profile = response.json()
    assert set(profile) == {
        "relationship_id",
        "student_id",
        "display_name",
        "username_display",
        "linked_at",
        "removed_at",
        "homework",
        "actions",
    }
    assert profile["relationship_id"] == str(relationship.id)
    assert profile["display_name"] == "Name unavailable"
    assert profile["username_display"] == "No username"
    assert profile["linked_at"] is None
    assert profile["removed_at"] is None
    assert profile["homework"] == {
        "assigned": 3,
        "completed": 2,
        "incomplete": 1,
        "completed_by_student": 1,
        "completed_by_teacher": 1,
    }
    assert [action["id"] for action in profile["actions"]] == [
        str(uuid.UUID(int=index)) for index in range(12, 2, -1)
    ]
    assert profile["actions"][0]["resource_available"] is True
    assert profile["actions"][1]["resource_available"] is False
    assert all(
        action["resource_available"] is None for action in profile["actions"][2:]
    )
    assert all(
        "private" not in str(profile[key]).lower()
        for key in ("display_name", "username_display")
    )
    assert "email" not in profile and "telegram_user_id" not in profile

    owner_profile = await client.get(
        f"/api/v1/auth/users/{student.id}/student-profile",
        headers=_headers(teacher),
    )
    assert owner_profile.status_code == 403
    student_access = await client.get(
        f"/api/v1/auth/teacher/students/profiles/{student.id}",
        headers=_headers(student),
    )
    assert student_access.status_code == 403


async def test_former_profile_history_is_scoped_to_its_relationship(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    former_teacher = await _teacher(db_session, "Former teacher")
    new_teacher = await _teacher(db_session, "New teacher")
    unrelated_teacher = await _teacher(db_session, "Unrelated teacher")
    student = User(
        role="STUDENT",
        timezone="UTC",
        teacher_id=new_teacher.id,
        full_name="Student",
        telegram_username="student_name",
    )
    db_session.add(student)
    await db_session.flush()

    former_relationship = await _relationship(
        db_session,
        former_teacher,
        student,
        linked_at=datetime(2025, 1, 1, tzinfo=UTC),
        removed_at=datetime(2025, 5, 1, tzinfo=UTC),
    )
    current_relationship = await _relationship(
        db_session,
        new_teacher,
        student,
        linked_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    past_lesson = Event(
        teacher_id=former_teacher.id,
        user_id=student.id,
        event_type="LESSON",
        start=datetime(2025, 4, 1, 10),
        end=datetime(2025, 4, 1, 11),
    )
    db_session.add(past_lesson)
    await db_session.flush()
    db_session.add_all(
        [
            StudentAction(
                relationship_id=former_relationship.id,
                teacher_id=former_teacher.id,
                student_id=student.id,
                actor_id=student.id,
                action_type=StudentActionType.LESSON_CREATED,
                resource_type="lesson",
                resource_id=past_lesson.id,
                occurred_at=datetime(2025, 4, 1, 12, tzinfo=UTC),
            ),
            StudentAction(
                relationship_id=current_relationship.id,
                teacher_id=new_teacher.id,
                student_id=student.id,
                actor_id=student.id,
                action_type="current_action",
                occurred_at=datetime(2025, 7, 1, tzinfo=UTC),
            ),
        ]
    )
    await db_session.commit()

    former_list = await client.get(
        "/api/v1/auth/teacher/former-students",
        headers=_headers(former_teacher),
    )
    assert former_list.status_code == 200, former_list.text
    assert former_list.json()["total"] == 1
    assert former_list.json()["items"][0]["relationship_id"] == str(
        former_relationship.id
    )
    assert former_list.json()["items"][0]["username_display"] == "student_name"
    assert former_list.json()["items"][0]["linked_at"] == "2025-01-01T00:00:00+00:00"
    assert former_list.json()["items"][0]["removed_at"] == "2025-05-01T00:00:00+00:00"
    assert former_list.json()["items"][0]["homework"] == {
        "assigned": 0,
        "completed": 0,
        "incomplete": 0,
        "completed_by_student": 0,
        "completed_by_teacher": 0,
    }

    former_profile = await client.get(
        f"/api/v1/auth/teacher/former-students/{former_relationship.id}",
        headers=_headers(former_teacher),
    )
    assert former_profile.status_code == 200, former_profile.text
    assert [item["action_type"] for item in former_profile.json()["actions"]] == [
        StudentActionType.LESSON_CREATED
    ]
    assert former_profile.json()["actions"][0]["resource_available"] is True

    current_profile = await client.get(
        f"/api/v1/auth/teacher/students/profiles/{student.id}",
        headers=_headers(new_teacher),
    )
    assert current_profile.status_code == 200, current_profile.text
    assert current_profile.json()["relationship_id"] == str(current_relationship.id)
    assert [item["action_type"] for item in current_profile.json()["actions"]] == [
        "current_action"
    ]

    for teacher in (new_teacher, unrelated_teacher):
        denied = await client.get(
            f"/api/v1/auth/teacher/former-students/{former_relationship.id}",
            headers=_headers(teacher),
        )
        assert denied.status_code == 404
    former_active_lookup = await client.get(
        f"/api/v1/auth/teacher/students/profiles/{student.id}",
        headers=_headers(former_teacher),
    )
    assert former_active_lookup.status_code == 404
    student_access = await client.get(
        "/api/v1/auth/teacher/former-students",
        headers=_headers(student),
    )
    assert student_access.status_code == 403


async def test_profile_roster_queries_are_bounded_and_paged(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher = await _teacher(db_session, "Roster teacher")
    for index in range(20):
        student = User(
            role="STUDENT",
            timezone="UTC",
            teacher_id=teacher.id,
            full_name=f"Student {index:02}",
        )
        db_session.add(student)
        await db_session.flush()
        await _relationship(
            db_session,
            teacher,
            student,
            linked_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    await db_session.commit()

    statement_count = 0

    def count_statement(*_args: object) -> None:
        nonlocal statement_count
        statement_count += 1

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        response = await client.get(
            "/api/v1/auth/teacher/students/profiles",
            params={"skip": 5, "limit": 7},
            headers=_headers(teacher),
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 20
    assert len(response.json()["items"]) == 7
    assert statement_count <= 6
    assert all(
        item["homework"]
        == {
            "assigned": 0,
            "completed": 0,
            "incomplete": 0,
            "completed_by_student": 0,
            "completed_by_teacher": 0,
        }
        for item in response.json()["items"]
    )
