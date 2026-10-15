from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.range_service import load_raw_occurrences_for_validation
from backend.exceptions import ValidationError


async def _register_and_login(
    client: AsyncClient,
    email: str,
    role: str,
    timezone: str = "UTC",
) -> tuple[str, str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "timezone": timezone,
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


async def _teacher_and_students(
    client: AsyncClient, suffix: str, timezone: str = "UTC"
) -> tuple[str, str, list[tuple[str, str]]]:
    teacher_id, teacher_token = await _register_and_login(
        client, f"{suffix}-teacher@test.com", "TEACHER", timezone
    )
    profile = await client.post(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        json={"code": f"join-{suffix}"},
        headers=_headers(teacher_token),
    )
    assert profile.status_code == 201, profile.text

    students: list[tuple[str, str]] = []
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


async def test_later_recurrence_conflict_cancellation_and_adjacency(
    client: AsyncClient,
    db_session: AsyncSession,  # noqa: ARG001
) -> None:
    _, teacher_token, students = await _teacher_and_students(client, "later-conflict")
    student_id = students[0][0]
    headers = _headers(teacher_token)
    recurring = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "interval": 7,
            "interval_end": "2026-12-29T09:00:00Z",
            "user_id": student_id,
        },
    )
    assert recurring.status_code == 201, recurring.text
    recurring_id = recurring.json()["id"]

    adjacent = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-15T09:30:00Z",
            "end": "2026-12-15T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert adjacent.status_code == 201, adjacent.text

    overlapping_payload = {
        "start": "2026-12-15T09:00:00Z",
        "end": "2026-12-15T09:20:00Z",
        "user_id": student_id,
    }
    blocked = await client.post(
        "/api/v1/lessons/", headers=headers, json=overlapping_payload
    )
    assert blocked.status_code == 422

    update = await client.put(
        f"/api/v1/recurrent-lessons/{recurring_id}",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "interval": 7,
            "interval_end": "2026-12-29T09:00:00Z",
            "user_id": student_id,
            "cancels": [
                {
                    "start": "2026-12-15T09:00:00Z",
                    "end": "2026-12-15T09:30:00Z",
                }
            ],
        },
    )
    assert update.status_code == 200, update.text
    allowed_after_cancel = await client.post(
        "/api/v1/lessons/", headers=headers, json=overlapping_payload
    )
    assert allowed_after_cancel.status_code == 201, allowed_after_cancel.text


async def test_recurring_conflicts_cover_full_horizon_and_reject_self_overlap(
    client: AsyncClient,
    db_session: AsyncSession,  # noqa: ARG001
) -> None:
    _, teacher_token, students = await _teacher_and_students(client, "long-horizon")
    student_id = students[0][0]
    headers = _headers(teacher_token)
    long_series = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-01-01T09:00:00Z",
            "end": "2026-01-01T09:30:00Z",
            "interval": 1,
            "interval_end": "2035-01-01T09:00:00Z",
            "user_id": student_id,
        },
    )
    assert long_series.status_code == 201, long_series.text

    exact_horizon = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-01-01T11:00:00Z",
            "end": "2026-01-01T11:30:00Z",
            "interval": 7,
            "interval_end": "2036-01-01T11:00:00Z",
            "user_id": student_id,
        },
    )
    assert exact_horizon.status_code == 201, exact_horizon.text

    far_conflict = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2034-04-10T09:15:00Z",
            "end": "2034-04-10T09:45:00Z",
            "interval": 7,
            "interval_end": "2034-05-01T09:15:00Z",
            "user_id": student_id,
        },
    )
    assert far_conflict.status_code == 422

    beyond_horizon = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-01-01T09:00:00Z",
            "end": "2026-01-01T09:30:00Z",
            "interval": 7,
            "interval_end": "2036-01-02T09:00:00Z",
            "user_id": student_id,
        },
    )
    assert beyond_horizon.status_code == 422

    self_overlapping = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-02T09:30:00Z",
            "interval": 1,
            "interval_end": "2026-12-04T09:00:00Z",
            "user_id": student_id,
        },
    )
    assert self_overlapping.status_code == 422


async def test_full_horizon_validation_fails_explicitly_over_occurrence_budget(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, _, students = await _teacher_and_students(client, "occurrence-budget")
    start = datetime(2026, 1, 1, 9)
    end = datetime(2026, 1, 1, 9, 30)
    interval_end = datetime(2036, 1, 1, 9)
    db_session.add_all(
        RecurrentEvent(
            teacher_id=UUID(teacher_id),
            user_id=UUID(students[index % len(students)][0]),
            event_type=EventType.LESSON.value,
            start=start,
            end=end,
            interval=1,
            interval_end=interval_end,
            recurrence_mode=RecurrenceMode.TEACHER_LOCAL.value,
            recurrence_timezone="UTC",
        )
        for index in range(3)
    )
    await db_session.commit()

    with pytest.raises(ValidationError, match="exceeds the limit"):
        await load_raw_occurrences_for_validation(
            db_session,
            UUID(teacher_id),
            start.replace(tzinfo=UTC),
            interval_end.replace(tzinfo=UTC),
        )


async def test_recurring_conflicts_handle_midnight_and_different_intervals(
    client: AsyncClient,
    db_session: AsyncSession,  # noqa: ARG001
) -> None:
    _, teacher_token, students = await _teacher_and_students(client, "overnight")
    student_id = students[0][0]
    headers = _headers(teacher_token)
    overnight = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T23:30:00Z",
            "end": "2026-12-02T00:30:00Z",
            "interval": 2,
            "interval_end": "2026-12-09T23:30:00Z",
            "user_id": student_id,
        },
    )
    assert overnight.status_code == 201, overnight.text

    different_interval = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-02T00:15:00Z",
            "end": "2026-12-02T00:45:00Z",
            "interval": 3,
            "interval_end": "2026-12-11T00:15:00Z",
            "user_id": student_id,
        },
    )
    assert different_interval.status_code == 422


async def test_dst_shifted_occurrence_participates_in_conflict_check(
    client: AsyncClient,
    db_session: AsyncSession,  # noqa: ARG001
) -> None:
    _, teacher_token, students = await _teacher_and_students(
        client, "dst-conflict", "Europe/Berlin"
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)
    recurring = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-03-22T01:30:00Z",
            "end": "2026-03-22T02:15:00Z",
            "interval": 7,
            "interval_end": "2026-04-12T01:30:00Z",
            "user_id": student_id,
        },
    )
    assert recurring.status_code == 201, recurring.text

    dst_occurrence = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-03-29T01:45:00Z",
            "end": "2026-03-29T02:00:00Z",
            "user_id": student_id,
        },
    )
    assert dst_occurrence.status_code == 422


async def test_vacations_block_applicable_one_offs_but_not_recurring_events(
    client: AsyncClient,
    db_session: AsyncSession,  # noqa: ARG001
) -> None:
    teacher_id, teacher_token, students = await _teacher_and_students(
        client, "vacation-conflict"
    )
    student_a, student_a_token = students[0]
    student_b, _ = students[1]
    teacher_headers = _headers(teacher_token)
    teacher_vacation = await client.post(
        "/api/v1/vacations/",
        headers=teacher_headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T10:00:00Z",
            "user_id": teacher_id,
        },
    )
    assert teacher_vacation.status_code == 201, teacher_vacation.text

    recurring_during_teacher_vacation = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=teacher_headers,
        json={
            "start": "2026-12-01T09:30:00Z",
            "end": "2026-12-01T10:00:00Z",
            "interval": 7,
            "interval_end": "2026-12-15T09:30:00Z",
            "user_id": student_a,
        },
    )
    assert recurring_during_teacher_vacation.status_code == 201

    student_vacation = await client.post(
        "/api/v1/vacations/",
        headers=_headers(student_a_token),
        json={
            "start": "2026-12-02T09:00:00Z",
            "end": "2026-12-02T10:00:00Z",
            "user_id": student_a,
        },
    )
    assert student_vacation.status_code == 201, student_vacation.text

    unaffected_student = await client.post(
        "/api/v1/lessons/",
        headers=teacher_headers,
        json={
            "start": "2026-12-02T09:15:00Z",
            "end": "2026-12-02T09:45:00Z",
            "user_id": student_b,
        },
    )
    assert unaffected_student.status_code == 201, unaffected_student.text

    blocked_student = await client.post(
        "/api/v1/lessons/",
        headers=teacher_headers,
        json={
            "start": "2026-12-02T09:15:00Z",
            "end": "2026-12-02T09:45:00Z",
            "user_id": student_a,
        },
    )
    assert blocked_student.status_code == 422

    lesson_before_vacation_change = await client.post(
        "/api/v1/lessons/",
        headers=teacher_headers,
        json={
            "start": "2026-12-03T09:00:00Z",
            "end": "2026-12-03T09:30:00Z",
            "user_id": student_b,
        },
    )
    assert lesson_before_vacation_change.status_code == 201
    vacation_update = await client.put(
        f"/api/v1/vacations/{teacher_vacation.json()['id']}",
        headers=teacher_headers,
        json={
            "start": "2026-12-03T09:00:00Z",
            "end": "2026-12-03T10:00:00Z",
            "user_id": teacher_id,
        },
    )
    assert vacation_update.status_code == 200, vacation_update.text


async def test_one_off_break_preview_and_confirmed_update_displace_lessons(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, teacher_token, students = await _teacher_and_students(
        client, "break-one-off"
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)

    lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": student_id,
        },
    )
    assert lesson.status_code == 201, lesson.text
    lesson_id = UUID(lesson.json()["id"])
    recurring_lesson_id = uuid4()
    db_session.add(
        RecurrentEvent(
            id=recurring_lesson_id,
            teacher_id=UUID(teacher_id),
            user_id=UUID(student_id),
            event_type=EventType.LESSON.value,
            start=datetime(2026, 12, 1, 9),
            end=datetime(2026, 12, 1, 9, 30),
            interval=7,
            interval_end=datetime(2026, 12, 8, 9),
            recurrence_mode=RecurrenceMode.UTC_STEP.value,
            recurrence_timezone=None,
        )
    )
    await db_session.commit()
    student_vacation = await client.post(
        "/api/v1/vacations/",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": student_id,
        },
    )
    assert student_vacation.status_code == 201, student_vacation.text
    break_payload = {
        "start": "2026-12-01T09:00:00Z",
        "end": "2026-12-01T09:30:00Z",
        "user_id": teacher_id,
    }
    preview = await client.post("/api/v1/breaks/", headers=headers, json=break_payload)
    assert preview.status_code == 409
    affected = preview.json()["detail"]["affected_occurrences"]
    assert len(affected) == 2
    assert {item["source_id"] for item in affected} == {
        str(lesson_id),
        str(recurring_lesson_id),
    }
    assert await db_session.get(Event, lesson_id) is not None
    assert (
        await db_session.scalar(
            select(func.count(RecurrentCancels.id)).where(
                RecurrentCancels.recurrent_event_id == recurring_lesson_id
            )
        )
        == 0
    )

    confirmed = await client.post(
        "/api/v1/breaks/?confirm_displacement=true",
        headers=headers,
        json=break_payload,
    )
    assert confirmed.status_code == 201, confirmed.text
    assert await db_session.get(Event, lesson_id) is None
    displaced_recurrent = await db_session.scalar(
        select(RecurrentCancels).where(
            RecurrentCancels.recurrent_event_id == recurring_lesson_id
        )
    )
    assert displaced_recurrent is not None
    assert displaced_recurrent.start == datetime(2026, 12, 1, 9)

    second_lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-02T10:00:00Z",
            "end": "2026-12-02T10:30:00Z",
            "user_id": student_id,
        },
    )
    assert second_lesson.status_code == 201, second_lesson.text
    break_outside_lesson = await client.post(
        "/api/v1/breaks/",
        headers=headers,
        json={
            "start": "2026-12-02T11:00:00Z",
            "end": "2026-12-02T11:30:00Z",
            "user_id": teacher_id,
        },
    )
    assert break_outside_lesson.status_code == 201, break_outside_lesson.text
    break_id = break_outside_lesson.json()["id"]
    update_payload = {
        "start": "2026-12-02T10:00:00Z",
        "end": "2026-12-02T10:30:00Z",
        "user_id": teacher_id,
    }
    update_preview = await client.put(
        f"/api/v1/breaks/{break_id}", headers=headers, json=update_payload
    )
    assert update_preview.status_code == 409
    stored_break = await db_session.get(Event, UUID(break_id))
    assert stored_break is not None
    assert stored_break.start == datetime(2026, 12, 2, 11)

    updated = await client.put(
        f"/api/v1/breaks/{break_id}?confirm_displacement=true",
        headers=headers,
        json=update_payload,
    )
    assert updated.status_code == 200, updated.text
    assert (
        await db_session.scalar(
            select(Event).where(Event.id == UUID(second_lesson.json()["id"]))
        )
        is None
    )


async def test_recurring_break_create_and_update_displace_exact_occurrences(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, teacher_token, students = await _teacher_and_students(
        client, "break-recurring"
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)

    lesson_series = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-08T10:00:00Z",
            "end": "2026-12-08T10:30:00Z",
            "interval": 7,
            "interval_end": "2027-01-05T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert lesson_series.status_code == 201, lesson_series.text
    lesson_series_id = UUID(lesson_series.json()["id"])
    break_payload = {
        "start": "2026-12-08T10:00:00Z",
        "end": "2026-12-08T10:30:00Z",
        "interval": 7,
        "interval_end": "2027-01-05T10:00:00Z",
        "user_id": teacher_id,
    }
    preview = await client.post(
        "/api/v1/recurrent-breaks/", headers=headers, json=break_payload
    )
    assert preview.status_code == 409
    assert len(preview.json()["detail"]["affected_occurrences"]) == 4
    assert (
        await db_session.scalar(
            select(func.count(RecurrentCancels.id)).where(
                RecurrentCancels.recurrent_event_id == lesson_series_id
            )
        )
        == 0
    )

    confirmed = await client.post(
        "/api/v1/recurrent-breaks/?confirm_displacement=true",
        headers=headers,
        json=break_payload,
    )
    assert confirmed.status_code == 201, confirmed.text
    assert (
        await db_session.scalar(
            select(func.count(RecurrentCancels.id)).where(
                RecurrentCancels.recurrent_event_id == lesson_series_id
            )
        )
        == 4
    )
    await client.delete(
        f"/api/v1/recurrent-breaks/{confirmed.json()['id']}", headers=headers
    )
    assert (
        await db_session.scalar(
            select(func.count(RecurrentCancels.id)).where(
                RecurrentCancels.recurrent_event_id == lesson_series_id
            )
        )
        == 4
    )

    second_series = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-08T12:00:00Z",
            "end": "2026-12-08T12:30:00Z",
            "interval": 7,
            "interval_end": "2027-01-05T12:00:00Z",
            "user_id": student_id,
        },
    )
    assert second_series.status_code == 201, second_series.text
    second_break_payload = {
        "start": "2026-12-08T13:00:00Z",
        "end": "2026-12-08T13:30:00Z",
        "interval": 7,
        "interval_end": "2027-01-05T13:00:00Z",
        "user_id": teacher_id,
    }
    second_break = await client.post(
        "/api/v1/recurrent-breaks/", headers=headers, json=second_break_payload
    )
    assert second_break.status_code == 201, second_break.text
    update_payload = {
        **second_break_payload,
        "start": "2026-12-08T12:00:00Z",
        "end": "2026-12-08T12:30:00Z",
    }
    update_preview = await client.put(
        f"/api/v1/recurrent-breaks/{second_break.json()['id']}",
        headers=headers,
        json=update_payload,
    )
    assert update_preview.status_code == 409
    stored_break = await db_session.get(RecurrentEvent, UUID(second_break.json()["id"]))
    assert stored_break is not None
    assert stored_break.start == datetime(2026, 12, 8, 13)

    updated = await client.put(
        f"/api/v1/recurrent-breaks/{second_break.json()['id']}?confirm_displacement=true",
        headers=headers,
        json=update_payload,
    )
    assert updated.status_code == 200, updated.text
    assert (
        await db_session.scalar(
            select(func.count(RecurrentCancels.id)).where(
                RecurrentCancels.recurrent_event_id == UUID(second_series.json()["id"])
            )
        )
        == 4
    )
