from datetime import datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(
    client: AsyncClient, email: str, role: str, full_name: str
) -> tuple[str, str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "full_name": full_name,
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


async def _teacher_with_students(
    client: AsyncClient,
) -> tuple[str, str, tuple[str, str], tuple[str, str]]:
    teacher_id, teacher_token = await _register_and_login(
        client, "management-teacher@test.com", "TEACHER", "Teacher"
    )
    profile = await client.post(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        headers=_headers(teacher_token),
        json={"code": "t_MGMT01"},
    )
    assert profile.status_code == 201, profile.text

    students: list[tuple[str, str]] = []
    for letter in ("a", "b"):
        student_id, student_token = await _register_and_login(
            client,
            f"management-student-{letter}@test.com",
            "STUDENT",
            f"Student {letter.upper()}",
        )
        linked = await client.post(
            "/api/v1/auth/me/teacher",
            headers=_headers(student_token),
            json={"teacher_code": "t_MGMT01"},
        )
        assert linked.status_code == 200, linked.text
        students.append((student_id, student_token))
    return teacher_id, teacher_token, students[0], students[1]


async def test_management_reads_include_hidden_and_cancelled_events_without_leaks(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, teacher_token, student_a, student_b = await _teacher_with_students(
        client
    )
    teacher_uuid = UUID(teacher_id)
    student_a_id = UUID(student_a[0])
    student_b_id = UUID(student_b[0])

    hidden_lesson = Event(
        teacher_id=teacher_uuid,
        user_id=student_a_id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 12, 1, 9),
        end=datetime(2026, 12, 1, 10),
    )
    visible_lesson = Event(
        teacher_id=teacher_uuid,
        user_id=student_a_id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 12, 2, 9),
        end=datetime(2026, 12, 2, 10),
    )
    other_student_lesson = Event(
        teacher_id=teacher_uuid,
        user_id=student_b_id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 12, 3, 9),
        end=datetime(2026, 12, 3, 10),
    )
    vacation = Event(
        teacher_id=teacher_uuid,
        user_id=student_a_id,
        event_type=EventType.VACATION.value,
        start=datetime(2026, 12, 1, 9, 30),
        end=datetime(2026, 12, 1, 10, 30),
    )
    series = RecurrentEvent(
        teacher_id=teacher_uuid,
        user_id=student_a_id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 12, 1, 9),
        end=datetime(2026, 12, 1, 10),
        interval=7,
        interval_end=datetime(2026, 12, 29, 9),
        recurrence_mode="UTC_STEP",
        recurrence_timezone=None,
    )
    db_session.add_all(
        [
            hidden_lesson,
            visible_lesson,
            other_student_lesson,
            vacation,
            series,
        ]
    )
    await db_session.flush()
    cancellation = RecurrentCancels(
        recurrent_event_id=series.id,
        start=datetime(2026, 12, 8, 9),
        end=datetime(2026, 12, 8, 10),
    )
    recurring_vacation = Event(
        teacher_id=teacher_uuid,
        user_id=student_a_id,
        event_type=EventType.VACATION.value,
        start=datetime(2026, 12, 15, 9, 30),
        end=datetime(2026, 12, 15, 10, 30),
    )
    manual_break = Event(
        teacher_id=teacher_uuid,
        user_id=teacher_uuid,
        event_type=EventType.BREAK.value,
        start=datetime(2026, 12, 4, 9),
        end=datetime(2026, 12, 4, 9, 15),
    )
    recurrent_break = RecurrentEvent(
        teacher_id=teacher_uuid,
        user_id=teacher_uuid,
        event_type=EventType.BREAK.value,
        start=datetime(2026, 12, 5, 9),
        end=datetime(2026, 12, 5, 9, 15),
        interval=7,
        interval_end=datetime(2026, 12, 26, 9),
        recurrence_mode="UTC_STEP",
        recurrence_timezone=None,
    )
    db_session.add_all(
        [cancellation, recurring_vacation, manual_break, recurrent_break]
    )
    await db_session.commit()

    first_page = await client.get(
        "/api/v1/lessons/",
        params={"subject_id": str(student_a_id), "skip": 0, "limit": 1},
        headers=_headers(teacher_token),
    )
    second_page = await client.get(
        "/api/v1/lessons/",
        params={"subject_id": str(student_a_id), "skip": 1, "limit": 1},
        headers=_headers(teacher_token),
    )
    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == second_page.json()["total"] == 2
    assert first_page.json()["items"][0]["id"] == str(hidden_lesson.id)
    assert first_page.json()["items"][0]["visibility_status"] == "hidden_by_vacation"
    assert second_page.json()["items"][0]["id"] == str(visible_lesson.id)
    assert second_page.json()["items"][0]["visibility_status"] == "visible"

    vacation_read = await client.get(
        f"/api/v1/vacations/{vacation.id}", headers=_headers(teacher_token)
    )
    assert vacation_read.status_code == 200, vacation_read.text
    assert vacation_read.json()["visibility_status"] == "not_applicable"
    vacation_list = await client.get(
        "/api/v1/vacations/",
        params={"subject_id": str(student_a_id)},
        headers=_headers(teacher_token),
    )
    assert vacation_list.status_code == 200, vacation_list.text
    assert vacation_list.json()["total"] == 2
    assert vacation.id in {UUID(item["id"]) for item in vacation_list.json()["items"]}

    break_read = await client.get(
        f"/api/v1/breaks/{manual_break.id}", headers=_headers(teacher_token)
    )
    assert break_read.status_code == 200, break_read.text
    assert break_read.json()["event_type"] == "BREAK"
    recurrent_break_read = await client.get(
        f"/api/v1/recurrent-breaks/{recurrent_break.id}",
        headers=_headers(teacher_token),
    )
    assert recurrent_break_read.status_code == 200, recurrent_break_read.text
    assert recurrent_break_read.json()["is_recurrent"] is True
    assert recurrent_break_read.json()["interval"] == 7

    cancelled_read = await client.get(
        f"/api/v1/recurrent-lessons/{series.id}",
        params={"occurrence_start": "2026-12-08T09:00:00Z"},
        headers=_headers(teacher_token),
    )
    assert cancelled_read.status_code == 200, cancelled_read.text
    cancelled_detail = cancelled_read.json()
    assert cancelled_detail["recurrence_mode"] == "UTC_STEP"
    assert cancelled_detail["recurrence_timezone"] is None
    assert cancelled_detail["cancels"] == [
        {
            "start": "2026-12-08T09:00:00Z",
            "end": "2026-12-08T10:00:00Z",
        }
    ]
    assert cancelled_detail["inspected_occurrence"]["status"] == "cancelled"
    assert len(cancelled_detail["revision"]) == 64
    assert "email" not in cancelled_detail

    off_grid_occurrence = await client.get(
        f"/api/v1/recurrent-lessons/{series.id}",
        params={"occurrence_start": "2026-12-08T09:30:00Z"},
        headers=_headers(teacher_token),
    )
    assert off_grid_occurrence.status_code == 422

    hidden_occurrence_read = await client.get(
        f"/api/v1/recurrent-lessons/{series.id}",
        params={"occurrence_start": "2026-12-15T09:00:00Z"},
        headers=_headers(teacher_token),
    )
    assert hidden_occurrence_read.status_code == 200, hidden_occurrence_read.text
    assert (
        hidden_occurrence_read.json()["inspected_occurrence"]["status"]
        == "hidden_by_vacation"
    )
    recurrent_list = await client.get(
        "/api/v1/recurrent-lessons/",
        params={"subject_id": str(student_a_id)},
        headers=_headers(teacher_token),
    )
    assert recurrent_list.status_code == 200, recurrent_list.text
    assert recurrent_list.json()["items"][0]["cancelled_occurrences"] == 1
    original_revision = cancelled_detail["revision"]
    series.interval_end = datetime(2027, 1, 5, 9)
    await db_session.commit()
    changed_revision = await client.get(
        f"/api/v1/recurrent-lessons/{series.id}",
        headers=_headers(teacher_token),
    )
    assert changed_revision.status_code == 200, changed_revision.text
    assert changed_revision.json()["revision"] != original_revision

    student_cross_read = await client.get(
        f"/api/v1/lessons/{other_student_lesson.id}",
        headers=_headers(student_a[1]),
    )
    assert student_cross_read.status_code == 403
    type_confused_read = await client.get(
        f"/api/v1/breaks/{hidden_lesson.id}", headers=_headers(teacher_token)
    )
    assert type_confused_read.status_code == 422
    student_break_list = await client.get(
        "/api/v1/breaks/", headers=_headers(student_a[1])
    )
    assert student_break_list.status_code == 403
    student_subject_filter = await client.get(
        "/api/v1/lessons/",
        params={"subject_id": str(student_b_id)},
        headers=_headers(student_a[1]),
    )
    assert student_subject_filter.status_code == 403


async def test_teacher_context_is_minimal_and_local_resolution_uses_saved_timezone(
    client: AsyncClient,
) -> None:
    teacher_id, teacher_token, student, _ = await _teacher_with_students(client)
    profile_update = await client.put(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        headers=_headers(teacher_token),
        json={
            "work_start": "08:00",
            "work_end": "17:00",
            "working_days": [1, 2, 3, 4, 5],
            "lesson_length": 45,
        },
    )
    assert profile_update.status_code == 200, profile_update.text

    context = await client.get(
        "/api/v1/auth/me/teacher-context", headers=_headers(student[1])
    )
    assert context.status_code == 200, context.text
    assert context.json() == {
        "teacher_id": teacher_id,
        "timezone": "UTC",
        "work_start": "08:00",
        "work_end": "17:00",
        "working_days": [1, 2, 3, 4, 5],
        "lesson_length": 45,
    }
    owner_only_profile = await client.get(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        headers=_headers(student[1]),
    )
    assert owner_only_profile.status_code == 403

    timezone_update = await client.patch(
        "/api/v1/auth/me",
        headers=_headers(student[1]),
        json={"timezone": "America/New_York"},
    )
    assert timezone_update.status_code == 200, timezone_update.text

    gap = await client.post(
        "/api/v1/events/resolve-local-time",
        headers=_headers(student[1]),
        json={
            "start": "2026-03-08T02:30:00",
            "end": "2026-03-08T04:30:00",
        },
    )
    assert gap.status_code == 200, gap.text
    assert gap.json()["timezone"] == "America/New_York"
    assert gap.json()["start"] == {
        "local": "2026-03-08T03:30:00-04:00",
        "utc": "2026-03-08T07:30:00+00:00",
        "utc_offset": "-04:00",
    }

    fold = await client.post(
        "/api/v1/events/resolve-local-time",
        headers=_headers(student[1]),
        json={
            "start": "2026-11-01T01:30:00",
            "end": "2026-11-01T02:30:00",
        },
    )
    assert fold.status_code == 200, fold.text
    assert fold.json()["start"]["utc"] == "2026-11-01T05:30:00+00:00"
    assert fold.json()["start"]["utc_offset"] == "-04:00"

    offset_input = await client.post(
        "/api/v1/events/resolve-local-time",
        headers=_headers(student[1]),
        json={
            "start": "2026-11-01T01:30:00-04:00",
            "end": "2026-11-01T02:30:00-05:00",
        },
    )
    assert offset_input.status_code == 422


async def test_unrelated_teacher_management_lists_are_empty(
    client: AsyncClient,
) -> None:
    _, _, _, _ = await _teacher_with_students(client)
    _, unrelated_token = await _register_and_login(
        client, "management-unrelated@test.com", "TEACHER", "Unrelated"
    )
    response = await client.get("/api/v1/lessons/", headers=_headers(unrelated_token))
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0}


async def test_unlinked_student_cannot_read_teacher_booking_context(
    client: AsyncClient,
) -> None:
    _, student_token = await _register_and_login(
        client, "management-unlinked@test.com", "STUDENT", "Unlinked"
    )
    response = await client.get(
        "/api/v1/auth/me/teacher-context", headers=_headers(student_token)
    )
    assert response.status_code == 400
