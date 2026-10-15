from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.models import Event, RecurrentCancels, RecurrentEvent


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
    user_id = registration.json()["id"]

    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert login.status_code == 200, login.text
    return user_id, login.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _teacher_with_students(
    client: AsyncClient, suffix: str
) -> tuple[str, str, list[tuple[str, str]]]:
    teacher_id, teacher_token = await _register_and_login(
        client, f"{suffix}-teacher@test.com", "TEACHER"
    )
    profile = await client.post(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        json={"code": f"join-{suffix}"},
        headers=_headers(teacher_token),
    )
    assert profile.status_code == 201, profile.text

    students = []
    for label in ("a", "b"):
        student_id, student_token = await _register_and_login(
            client, f"{suffix}-student-{label}@test.com", "STUDENT"
        )
        linked = await client.post(
            "/api/v1/auth/me/teacher",
            json={"teacher_code": f"join-{suffix}"},
            headers=_headers(student_token),
        )
        assert linked.status_code == 200, linked.text
        students.append((student_id, student_token))
    return teacher_id, teacher_token, students


async def test_students_cannot_mutate_other_students_events_but_teacher_can_reassign(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, teacher_token, students = await _teacher_with_students(
        client, "ownership"
    )
    student_a_id, student_a_token = students[0]
    student_b_id, _ = students[1]
    teacher_headers = _headers(teacher_token)
    student_a_headers = _headers(student_a_token)

    one_off = await client.post(
        "/api/v1/lessons/",
        headers=teacher_headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": student_b_id,
        },
    )
    assert one_off.status_code == 201, one_off.text
    one_off_id = UUID(one_off.json()["id"])

    recurring = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=teacher_headers,
        json={
            "start": "2026-12-08T09:00:00Z",
            "end": "2026-12-08T09:30:00Z",
            "interval": 7,
            "interval_end": "2026-12-29T09:00:00Z",
            "user_id": student_b_id,
        },
    )
    assert recurring.status_code == 201, recurring.text
    recurring_id = UUID(recurring.json()["id"])
    recurring_payload = {
        "start": "2026-12-08T09:00:00Z",
        "end": "2026-12-08T09:30:00Z",
        "interval": 7,
        "interval_end": "2026-12-29T09:00:00Z",
        "user_id": student_b_id,
        "cancels": [
            {
                "start": "2026-12-08T09:00:00Z",
                "end": "2026-12-08T09:30:00Z",
            }
        ],
    }
    canceled = await client.put(
        f"/api/v1/recurrent-lessons/{recurring_id}",
        headers=teacher_headers,
        json=recurring_payload,
    )
    assert canceled.status_code == 200, canceled.text
    cancellation_before = await db_session.scalar(
        select(RecurrentCancels).where(
            RecurrentCancels.recurrent_event_id == recurring_id
        )
    )
    assert cancellation_before is not None

    one_off_update = await client.put(
        f"/api/v1/lessons/{one_off_id}",
        headers=student_a_headers,
        json={
            "start": "2026-12-02T09:00:00Z",
            "end": "2026-12-02T09:30:00Z",
            "user_id": student_a_id,
        },
    )
    assert one_off_update.status_code == 403
    one_off_delete = await client.delete(
        f"/api/v1/lessons/{one_off_id}", headers=student_a_headers
    )
    assert one_off_delete.status_code == 403

    recurring_update = await client.put(
        f"/api/v1/recurrent-lessons/{recurring_id}",
        headers=student_a_headers,
        json={**recurring_payload, "user_id": student_a_id, "cancels": []},
    )
    assert recurring_update.status_code == 403
    recurring_delete = await client.delete(
        f"/api/v1/recurrent-lessons/{recurring_id}", headers=student_a_headers
    )
    assert recurring_delete.status_code == 403

    one_off_row = await db_session.get(Event, one_off_id)
    recurrent_row = await db_session.get(RecurrentEvent, recurring_id)
    cancellation_after = await db_session.scalar(
        select(RecurrentCancels).where(
            RecurrentCancels.recurrent_event_id == recurring_id
        )
    )
    assert one_off_row is not None and one_off_row.user_id == UUID(student_b_id)
    assert recurrent_row is not None and recurrent_row.user_id == UUID(student_b_id)
    assert cancellation_after is not None
    assert cancellation_after.id == cancellation_before.id

    teacher_reassignment = await client.put(
        f"/api/v1/lessons/{one_off_id}",
        headers=teacher_headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": student_a_id,
        },
    )
    assert teacher_reassignment.status_code == 200, teacher_reassignment.text
    assert teacher_reassignment.json()["user_id"] == student_a_id

    other_teacher_id, other_teacher_token = await _register_and_login(
        client, "ownership-other-teacher@test.com", "TEACHER"
    )
    assert other_teacher_id != teacher_id
    unrelated_delete = await client.delete(
        f"/api/v1/recurrent-lessons/{recurring_id}",
        headers=_headers(other_teacher_token),
    )
    assert unrelated_delete.status_code == 403


async def test_student_can_manage_own_lesson_and_vacation_but_not_breaks(
    client: AsyncClient,
) -> None:
    teacher_id, teacher_token, students = await _teacher_with_students(
        client, "student-access"
    )
    student_id, student_token = students[0]
    other_student_id, _ = students[1]
    headers = _headers(student_token)
    teacher_headers = _headers(teacher_token)

    lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-10T09:00:00Z",
            "end": "2026-12-10T09:30:00Z",
            "user_id": student_id,
        },
    )
    assert lesson.status_code == 201, lesson.text
    lesson_id = lesson.json()["id"]

    update = await client.put(
        f"/api/v1/lessons/{lesson_id}",
        headers=headers,
        json={
            "start": "2026-12-10T09:05:00Z",
            "end": "2026-12-10T09:35:00Z",
            "user_id": student_id,
            "event_type": "LESSON",
        },
    )
    assert update.status_code == 200, update.text

    reassignment = await client.put(
        f"/api/v1/lessons/{lesson_id}",
        headers=headers,
        json={
            "start": "2026-12-10T09:05:00Z",
            "end": "2026-12-10T09:35:00Z",
            "user_id": other_student_id,
        },
    )
    assert reassignment.status_code == 403

    vacation = await client.post(
        "/api/v1/vacations/",
        headers=headers,
        json={
            "start": "2026-12-10T11:00:00Z",
            "end": "2026-12-10T12:00:00Z",
            "user_id": student_id,
        },
    )
    assert vacation.status_code == 201, vacation.text
    vacation_id = vacation.json()["id"]

    recurrent_lesson = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2027-01-07T09:00:00Z",
            "end": "2027-01-07T09:30:00Z",
            "interval": 7,
            "interval_end": "2027-01-21T09:00:00Z",
            "user_id": student_id,
        },
    )
    assert recurrent_lesson.status_code == 201, recurrent_lesson.text
    recurrent_lesson_id = recurrent_lesson.json()["id"]
    recurrent_update = await client.put(
        f"/api/v1/recurrent-lessons/{recurrent_lesson_id}",
        headers=headers,
        json={
            "start": "2027-01-07T09:00:00Z",
            "end": "2027-01-07T09:30:00Z",
            "interval": 7,
            "interval_end": "2027-01-21T09:00:00Z",
            "user_id": student_id,
            "cancels": [],
        },
    )
    assert recurrent_update.status_code == 200, recurrent_update.text

    break_response = await client.post(
        "/api/v1/breaks/",
        headers=headers,
        json={
            "start": "2026-12-10T13:00:00Z",
            "end": "2026-12-10T13:30:00Z",
            "user_id": student_id,
        },
    )
    assert break_response.status_code == 403

    teacher_break = await client.post(
        "/api/v1/breaks/",
        headers=teacher_headers,
        json={
            "start": "2026-12-10T13:00:00Z",
            "end": "2026-12-10T13:30:00Z",
            "user_id": teacher_id,
        },
    )
    assert teacher_break.status_code == 201, teacher_break.text
    teacher_break_id = teacher_break.json()["id"]
    recurrent_teacher_break = await client.post(
        "/api/v1/recurrent-breaks/",
        headers=teacher_headers,
        json={
            "start": "2026-12-16T14:00:00Z",
            "end": "2026-12-16T14:30:00Z",
            "interval": 7,
            "interval_end": "2026-12-30T14:00:00Z",
            "user_id": teacher_id,
        },
    )
    assert recurrent_teacher_break.status_code == 201, recurrent_teacher_break.text
    recurrent_teacher_break_id = recurrent_teacher_break.json()["id"]

    break_updates = (
        (
            f"/api/v1/breaks/{teacher_break_id}",
            {
                "start": "2026-12-17T15:00:00Z",
                "end": "2026-12-17T15:30:00Z",
                "user_id": student_id,
            },
        ),
        (
            f"/api/v1/recurrent-breaks/{recurrent_teacher_break_id}",
            {
                "start": "2026-12-17T15:00:00Z",
                "end": "2026-12-17T15:30:00Z",
                "interval": 7,
                "interval_end": "2026-12-31T15:00:00Z",
                "user_id": student_id,
            },
        ),
    )
    for path, payload in break_updates:
        update = await client.put(
            path,
            headers=headers,
            json=payload,
        )
        assert update.status_code == 403
        delete = await client.delete(path, headers=headers)
        assert delete.status_code == 403

    assert (
        await client.delete(f"/api/v1/lessons/{lesson_id}", headers=headers)
    ).status_code == 204
    assert (
        await client.delete(
            f"/api/v1/recurrent-lessons/{recurrent_lesson_id}", headers=headers
        )
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/vacations/{vacation_id}", headers=headers)
    ).status_code == 204


async def test_domain_event_kinds_reject_spoofed_payloads_and_cross_domain_ids(
    client: AsyncClient,
) -> None:
    _, teacher_token = await _register_and_login(
        client, "event-kind-teacher@test.com", "TEACHER"
    )
    headers = _headers(teacher_token)
    me = await client.get("/api/v1/auth/me", headers=headers)
    teacher_id = me.json()["id"]

    one_off_spoofs = [
        ("/api/v1/lessons/", "BREAK"),
        ("/api/v1/breaks/", "LESSON"),
        ("/api/v1/vacations/", "LESSON"),
    ]
    for index, (path, event_type) in enumerate(one_off_spoofs):
        response = await client.post(
            path,
            headers=headers,
            json={
                "start": f"2026-12-{11 + index:02d}T09:00:00Z",
                "end": f"2026-12-{11 + index:02d}T09:30:00Z",
                "user_id": teacher_id,
                "event_type": event_type,
            },
        )
        assert response.status_code == 422

    recurring_spoofs = [
        ("/api/v1/recurrent-lessons/", "BREAK"),
        ("/api/v1/recurrent-breaks/", "LESSON"),
    ]
    for index, (path, event_type) in enumerate(recurring_spoofs):
        day = 20 + index
        response = await client.post(
            path,
            headers=headers,
            json={
                "start": f"2026-12-{day:02d}T09:00:00Z",
                "end": f"2026-12-{day:02d}T09:30:00Z",
                "interval": 7,
                "interval_end": f"2027-01-{day - 1:02d}T09:00:00Z",
                "user_id": teacher_id,
                "event_type": event_type,
            },
        )
        assert response.status_code == 422

    lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": teacher_id,
        },
    )
    assert lesson.status_code == 201, lesson.text
    lesson_id = lesson.json()["id"]
    recurrent = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-08T09:00:00Z",
            "end": "2026-12-08T09:30:00Z",
            "interval": 7,
            "interval_end": "2026-12-29T09:00:00Z",
            "user_id": teacher_id,
        },
    )
    assert recurrent.status_code == 201, recurrent.text
    recurrent_id = recurrent.json()["id"]

    for path in (
        f"/api/v1/breaks/{lesson_id}",
        f"/api/v1/vacations/{lesson_id}",
    ):
        update = await client.put(
            path,
            headers=headers,
            json={
                "start": "2026-12-01T10:00:00Z",
                "end": "2026-12-01T10:30:00Z",
                "user_id": teacher_id,
            },
        )
        assert update.status_code == 422
        delete = await client.delete(path, headers=headers)
        assert delete.status_code == 422

    recurrent_update = await client.put(
        f"/api/v1/recurrent-breaks/{recurrent_id}",
        headers=headers,
        json={
            "start": "2026-12-08T09:00:00Z",
            "end": "2026-12-08T09:30:00Z",
            "interval": 7,
            "interval_end": "2026-12-29T09:00:00Z",
            "user_id": teacher_id,
        },
    )
    assert recurrent_update.status_code == 422
    recurrent_delete = await client.delete(
        f"/api/v1/recurrent-breaks/{recurrent_id}", headers=headers
    )
    assert recurrent_delete.status_code == 422

    spoofed_update = await client.put(
        f"/api/v1/lessons/{lesson_id}",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": teacher_id,
            "event_type": "BREAK",
        },
    )
    assert spoofed_update.status_code == 422

    spoofed_recurrent_update = await client.put(
        f"/api/v1/recurrent-lessons/{recurrent_id}",
        headers=headers,
        json={
            "start": "2026-12-08T09:00:00Z",
            "end": "2026-12-08T09:30:00Z",
            "interval": 7,
            "interval_end": "2026-12-29T09:00:00Z",
            "user_id": teacher_id,
            "event_type": "BREAK",
        },
    )
    assert spoofed_recurrent_update.status_code == 422
