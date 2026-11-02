from datetime import datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.activity.constants import StudentActionType
from backend.activity.models import StudentAction


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(
    client: AsyncClient, email: str, role: str
) -> tuple[str, str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "timezone": "UTC",
        },
    )
    assert registration.status_code == 201, registration.text
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert login.status_code == 200, login.text
    return registration.json()["id"], login.json()["access_token"]


async def _teacher_and_students(
    client: AsyncClient, suffix: str, *, second_student: bool = False
) -> tuple[str, str, tuple[str, str], tuple[str, str] | None]:
    teacher_id, teacher_token = await _register_and_login(
        client, f"{suffix}-teacher@example.com", "TEACHER"
    )
    code = f"t_{suffix.upper()[:6]}"
    profile = await client.post(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        headers=_headers(teacher_token),
        json={"code": code, "working_days": [1, 2, 3, 4, 5, 6, 7]},
    )
    assert profile.status_code == 201, profile.text

    students: list[tuple[str, str]] = []
    labels = ("a", "b") if second_student else ("a",)
    for label in labels:
        student = await _register_and_login(
            client, f"{suffix}-student-{label}@example.com", "STUDENT"
        )
        linked = await client.post(
            "/api/v1/auth/me/teacher",
            headers=_headers(student[1]),
            json={"teacher_code": code},
        )
        assert linked.status_code == 200, linked.text
        students.append(student)
    return (
        teacher_id,
        teacher_token,
        students[0],
        students[1] if second_student else None,
    )


async def _actions(session: AsyncSession, student_id: str) -> list[StudentAction]:
    result = await session.scalars(
        select(StudentAction)
        .where(StudentAction.student_id == UUID(student_id))
        .order_by(StudentAction.occurred_at, StudentAction.id)
    )
    return list(result)


async def _schedule_actions(
    session: AsyncSession, student_id: str
) -> list[StudentAction]:
    schedule_types = [
        action.value
        for action in StudentActionType
        if action.name.startswith(("LESSON_", "VACATION_"))
    ]
    result = await session.scalars(
        select(StudentAction)
        .where(
            StudentAction.student_id == UUID(student_id),
            StudentAction.action_type.in_(schedule_types),
        )
        .order_by(StudentAction.occurred_at, StudentAction.id)
    )
    return list(result)


async def test_student_one_off_lesson_and_vacation_actions_are_recorded_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, _, student, _ = await _teacher_and_students(client, "schedule-oneoff")
    student_id, token = student
    headers = _headers(token)

    lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2027-01-04T09:00:00Z",
            "end": "2027-01-04T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert lesson.status_code == 201, lesson.text
    lesson_id = lesson.json()["id"]
    lesson_url = f"/api/v1/lessons/{lesson_id}"
    moved_lesson = {
        "start": "2027-01-05T09:00:00Z",
        "end": "2027-01-05T10:00:00Z",
        "user_id": student_id,
    }
    updated = await client.put(lesson_url, headers=headers, json=moved_lesson)
    replayed = await client.put(lesson_url, headers=headers, json=moved_lesson)
    assert updated.status_code == replayed.status_code == 200
    deleted = await client.delete(lesson_url, headers=headers)
    repeated_delete = await client.delete(lesson_url, headers=headers)
    assert deleted.status_code == 204
    assert repeated_delete.status_code == 422

    vacation = await client.post(
        "/api/v1/vacations/",
        headers=headers,
        json={
            "start": "2027-01-20T00:00:00Z",
            "end": "2027-01-21T00:00:00Z",
            "user_id": student_id,
        },
    )
    assert vacation.status_code == 201, vacation.text
    vacation_id = vacation.json()["id"]
    vacation_url = f"/api/v1/vacations/{vacation_id}"
    vacation_update = {
        "start": "2027-01-22T00:00:00Z",
        "end": "2027-01-23T00:00:00Z",
        "user_id": student_id,
    }
    updated_vacation = await client.put(
        vacation_url, headers=headers, json=vacation_update
    )
    assert updated_vacation.status_code == 200, updated_vacation.text
    deleted_vacation = await client.delete(vacation_url, headers=headers)
    assert deleted_vacation.status_code == 204

    actions = await _schedule_actions(db_session, student_id)
    assert [action.action_type for action in actions] == [
        StudentActionType.LESSON_CREATED,
        StudentActionType.LESSON_UPDATED,
        StudentActionType.LESSON_DELETED,
        StudentActionType.VACATION_CREATED,
        StudentActionType.VACATION_UPDATED,
        StudentActionType.VACATION_DELETED,
    ]
    assert [action.resource_id for action in actions[:3]] == [UUID(lesson_id)] * 3
    assert [action.resource_id for action in actions[3:]] == [UUID(vacation_id)] * 3
    assert [action.resource_type for action in actions] == [
        "lesson",
        "lesson",
        "lesson",
        "vacation",
        "vacation",
        "vacation",
    ]
    assert all(action.resource_occurrence_at is None for action in actions)
    assert len(await _actions(db_session, student_id)) == 7


async def test_student_series_and_occurrence_actions_include_exact_identity_and_rollback(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, teacher_token, student, other_student = await _teacher_and_students(
        client, "schedule-series", second_student=True
    )
    assert other_student is not None
    student_id, student_token = student
    series = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=_headers(student_token),
        json={
            "start": "2027-01-05T09:00:00Z",
            "end": "2027-01-05T10:00:00Z",
            "interval": 7,
            "interval_end": "2027-01-26T09:00:00Z",
            "user_id": student_id,
        },
    )
    assert series.status_code == 201, series.text
    series_id = series.json()["id"]
    series_url = f"/api/v1/recurrent-lessons/{series_id}"
    updated_series = {
        "start": "2027-01-05T10:00:00Z",
        "end": "2027-01-05T11:00:00Z",
        "interval": 7,
        "interval_end": "2027-01-26T10:00:00Z",
        "user_id": student_id,
    }
    update = await client.put(
        series_url, headers=_headers(student_token), json=updated_series
    )
    replay = await client.put(
        series_url, headers=_headers(student_token), json=updated_series
    )
    assert update.status_code == replay.status_code == 200

    blocking_lesson = await client.post(
        "/api/v1/lessons/",
        headers=_headers(teacher_token),
        json={
            "start": "2027-01-19T12:00:00Z",
            "end": "2027-01-19T13:00:00Z",
            "user_id": other_student[0],
        },
    )
    assert blocking_lesson.status_code == 201, blocking_lesson.text

    occurrence = {
        "occurrence_start": "2027-01-12T10:00:00Z",
        "occurrence_end": "2027-01-12T11:00:00Z",
    }
    cancel_url = f"{series_url}/occurrences/cancel"
    cancelled = await client.post(
        cancel_url, headers=_headers(student_token), json=occurrence
    )
    repeated_cancel = await client.post(
        cancel_url, headers=_headers(student_token), json=occurrence
    )
    assert cancelled.status_code == 200, cancelled.text
    assert repeated_cancel.status_code == 409

    move_url = f"{series_url}/occurrences/move"
    failed_move = await client.post(
        move_url,
        headers=_headers(student_token),
        json={
            "occurrence_start": "2027-01-19T10:00:00Z",
            "occurrence_end": "2027-01-19T11:00:00Z",
            "replacement_start": "2027-01-19T12:00:00Z",
            "replacement_end": "2027-01-19T13:00:00Z",
        },
    )
    assert failed_move.status_code == 422
    actions_before_move = await _schedule_actions(db_session, student_id)
    assert [action.action_type for action in actions_before_move] == [
        StudentActionType.LESSON_SERIES_CREATED,
        StudentActionType.LESSON_SERIES_UPDATED,
        StudentActionType.LESSON_OCCURRENCE_CANCELLED,
    ]

    moved_occurrence = {
        "occurrence_start": "2027-01-19T10:00:00Z",
        "occurrence_end": "2027-01-19T11:00:00Z",
        "replacement_start": "2027-01-20T09:00:00Z",
        "replacement_end": "2027-01-20T10:00:00Z",
    }
    moved = await client.post(
        move_url, headers=_headers(student_token), json=moved_occurrence
    )
    repeated_move = await client.post(
        move_url, headers=_headers(student_token), json=moved_occurrence
    )
    assert moved.status_code == 201, moved.text
    assert repeated_move.status_code == 409
    deleted = await client.delete(series_url, headers=_headers(student_token))
    assert deleted.status_code == 204

    actions = await _schedule_actions(db_session, student_id)
    assert [action.action_type for action in actions] == [
        StudentActionType.LESSON_SERIES_CREATED,
        StudentActionType.LESSON_SERIES_UPDATED,
        StudentActionType.LESSON_OCCURRENCE_CANCELLED,
        StudentActionType.LESSON_OCCURRENCE_MOVED,
        StudentActionType.LESSON_SERIES_DELETED,
    ]
    assert all(action.resource_id == UUID(series_id) for action in actions)
    assert [action.resource_type for action in actions] == [
        "lesson_series",
        "lesson_series",
        "lesson_occurrence",
        "lesson_occurrence",
        "lesson_series",
    ]
    assert [action.resource_occurrence_at for action in actions[2:4]] == [
        datetime(2027, 1, 12, 10),
        datetime(2027, 1, 19, 10),
    ]


async def test_teacher_break_displacement_does_not_create_student_delete_action(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, teacher_token, student, _ = await _teacher_and_students(
        client, "schedule-displacement"
    )
    student_id, student_token = student
    lesson = await client.post(
        "/api/v1/lessons/",
        headers=_headers(student_token),
        json={
            "start": "2027-01-04T09:00:00Z",
            "end": "2027-01-04T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert lesson.status_code == 201, lesson.text
    actions_before = await _schedule_actions(db_session, student_id)
    assert [action.action_type for action in actions_before] == [
        StudentActionType.LESSON_CREATED
    ]

    break_payload = {
        "start": "2027-01-04T09:00:00Z",
        "end": "2027-01-04T10:00:00Z",
        "user_id": teacher_id,
    }
    preview = await client.post(
        "/api/v1/breaks/", headers=_headers(teacher_token), json=break_payload
    )
    assert preview.status_code == 409, preview.text
    assert await _schedule_actions(db_session, student_id) == actions_before

    detail = preview.json()["detail"]
    confirmed = await client.post(
        "/api/v1/breaks/?confirm_displacement=true",
        headers={
            **_headers(teacher_token),
            "X-Displacement-Token": detail["confirmation_token"],
        },
        json=break_payload,
    )
    assert confirmed.status_code == 201, confirmed.text
    assert await _schedule_actions(db_session, student_id) == actions_before
    assert not await db_session.scalar(
        select(StudentAction).where(
            StudentAction.student_id == UUID(student_id),
            StudentAction.action_type == StudentActionType.LESSON_DELETED,
        )
    )
