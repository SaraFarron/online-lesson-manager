from datetime import UTC, datetime, timedelta
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
    client: AsyncClient,
    suffix: str,
    timezone: str = "UTC",
    work_start: str = "09:00",
    break_minutes: int = 0,
) -> tuple[str, str, list[tuple[str, str]]]:
    teacher_id, teacher_token = await _register_and_login(
        client, f"{suffix}-teacher@test.com", "TEACHER", timezone
    )
    profile = await client.post(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        json={
            "code": f"join-{suffix}",
            "work_start": work_start,
            "working_days": [1, 2, 3, 4, 5, 6, 7],
            "between_lessons_break": break_minutes,
        },
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


@pytest.mark.parametrize(
    ("blocking_date", "expected_status"),
    [
        ("2027-10-26", 422),
        ("2027-10-27", 201),
    ],
)
async def test_open_weekly_lesson_validates_before_exclusive_year_boundary(
    client: AsyncClient,
    db_session: AsyncSession,
    blocking_date: str,
    expected_status: int,
) -> None:
    _, teacher_token, students = await _teacher_and_students(
        client, f"open-boundary-{blocking_date[-2:]}"
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)
    existing = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": f"{blocking_date}T09:00:00Z",
            "end": f"{blocking_date}T09:30:00Z",
            "user_id": student_id,
        },
    )
    assert existing.status_code == 201, existing.text

    response = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-10-27T09:00:00Z",
            "end": "2026-10-27T09:30:00Z",
            "interval": 7,
            "interval_end": None,
            "user_id": student_id,
        },
    )

    assert response.status_code == expected_status, response.text
    if expected_status == 201:
        series = await db_session.get(RecurrentEvent, UUID(response.json()["id"]))
        assert series is not None
        assert series.interval_end is None


@pytest.mark.parametrize(
    ("preferences", "break_minutes", "existing_time", "error"),
    [
        (
            {"max_lessons_per_day": 1, "max_consecutive_lessons": 1},
            0,
            "11:00",
            "daily lesson limit of 1",
        ),
        (
            {"max_consecutive_lessons": 1},
            30,
            "10:00",
            "consecutive lesson limit of 1",
        ),
    ],
)
async def test_open_series_applies_daily_and_consecutive_limits_within_year(
    client: AsyncClient,
    preferences: dict[str, int],
    break_minutes: int,
    existing_time: str,
    error: str,
) -> None:
    teacher_id, teacher_token, students = await _teacher_and_students(
        client,
        f"open-limit-{existing_time[:2]}",
        break_minutes=break_minutes,
    )
    headers = _headers(teacher_token)
    profile_update = await client.put(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        headers=headers,
        json=preferences,
    )
    assert profile_update.status_code == 200, profile_update.text

    existing_start = datetime.fromisoformat(f"2027-10-26T{existing_time}:00+00:00")
    existing = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": existing_start.isoformat(),
            "end": (existing_start + timedelta(minutes=30)).isoformat(),
            "user_id": students[0][0],
        },
    )
    assert existing.status_code == 201, existing.text
    response = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-10-27T09:00:00Z",
            "end": "2026-10-27T09:30:00Z",
            "interval": 7,
            "interval_end": None,
            "user_id": students[0][0],
        },
    )

    assert response.status_code == 422
    assert error in response.text


async def test_open_series_checks_automatic_break_tail_inside_year(
    client: AsyncClient,
) -> None:
    _, teacher_token, students = await _teacher_and_students(
        client, "open-break-tail", break_minutes=5
    )
    headers = _headers(teacher_token)
    existing = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2027-10-26T09:32:00Z",
            "end": "2027-10-26T09:40:00Z",
            "user_id": students[0][0],
        },
    )
    assert existing.status_code == 201, existing.text

    response = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-10-27T09:00:00Z",
            "end": "2026-10-27T09:30:00Z",
            "interval": 7,
            "interval_end": None,
            "user_id": students[0][0],
        },
    )

    assert response.status_code == 422
    assert "automatic post-lesson break" in response.text.lower()


async def test_finite_recurrence_retains_ten_year_horizon(
    client: AsyncClient,
) -> None:
    _, teacher_token, students = await _teacher_and_students(
        client, "finite-ten-year-horizon"
    )
    response = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=_headers(teacher_token),
        json={
            "start": "2026-10-27T09:00:00Z",
            "end": "2026-10-27T09:30:00Z",
            "interval": 7,
            "interval_end": "2037-01-01T09:00:00Z",
            "user_id": students[0][0],
        },
    )

    assert response.status_code == 422
    assert "cannot exceed 10 years" in response.text


async def test_one_off_write_detects_a_distant_open_ended_occurrence(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, teacher_token, students = await _teacher_and_students(client, "open-far-write")
    student_id = students[0][0]
    headers = _headers(teacher_token)
    created = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-10-27T09:00:00Z",
            "end": "2026-10-27T09:30:00Z",
            "interval": 7,
            "interval_end": None,
            "user_id": student_id,
        },
    )
    assert created.status_code == 201, created.text
    series = await db_session.get(RecurrentEvent, UUID(created.json()["id"]))
    assert series is not None
    assert series.interval_end is None

    distant_start = datetime(2026, 10, 27, 9, tzinfo=UTC) + timedelta(weeks=700)
    blocked = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": distant_start.isoformat(),
            "end": (distant_start + timedelta(minutes=30)).isoformat(),
            "user_id": student_id,
        },
    )

    assert blocked.status_code == 422
    assert "overlaps" in blocked.text
    finite_recurrence = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": distant_start.isoformat(),
            "end": (distant_start + timedelta(minutes=30)).isoformat(),
            "interval": 7,
            "interval_end": (distant_start + timedelta(days=14)).isoformat(),
            "user_id": student_id,
        },
    )
    assert finite_recurrence.status_code == 422
    assert "overlaps" in finite_recurrence.text


async def test_confirmed_finite_break_displaces_distant_open_occurrence(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, teacher_token, students = await _teacher_and_students(
        client, "open-break-displacement"
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)
    created = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-10-27T09:00:00Z",
            "end": "2026-10-27T09:30:00Z",
            "interval": 7,
            "interval_end": None,
            "user_id": student_id,
        },
    )
    assert created.status_code == 201, created.text
    series = await db_session.get(RecurrentEvent, UUID(created.json()["id"]))
    assert series is not None
    distant_start = datetime(2026, 10, 27, 9, tzinfo=UTC) + timedelta(weeks=700)
    break_payload = {
        "start": distant_start.isoformat(),
        "end": (distant_start + timedelta(hours=1)).isoformat(),
        "user_id": teacher_id,
    }

    preview = await client.post("/api/v1/breaks/", headers=headers, json=break_payload)
    assert preview.status_code == 409, preview.text
    detail = preview.json()["detail"]
    assert len(detail["affected_occurrences"]) == 1
    assert detail["affected_occurrences"][0]["source_id"] == str(series.id)

    confirmed = await client.post(
        "/api/v1/breaks/?confirm_displacement=true",
        headers={
            **headers,
            "X-Displacement-Token": detail["confirmation_token"],
        },
        json=break_payload,
    )
    assert confirmed.status_code == 201, confirmed.text
    cancellation = await db_session.scalar(
        select(RecurrentCancels).where(RecurrentCancels.recurrent_event_id == series.id)
    )
    assert cancellation is not None
    assert cancellation.start == distant_start.replace(tzinfo=None)
    after_displacement = await load_raw_occurrences_for_validation(
        db_session,
        UUID(teacher_id),
        distant_start,
        distant_start + timedelta(hours=2),
    )
    assert [item.source.event_type for item in after_displacement] == [EventType.BREAK]


async def test_automatic_break_admission_boundaries_and_full_series_horizon(
    client: AsyncClient,
) -> None:
    _, teacher_token, students = await _teacher_and_students(
        client, "auto-break-admission", break_minutes=5
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)

    first = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T10:00:00Z",
            "end": "2026-12-01T10:30:00Z",
            "user_id": student_id,
        },
    )
    assert first.status_code == 201, first.text

    too_early = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T10:34:00Z",
            "end": "2026-12-01T11:04:00Z",
            "user_id": student_id,
        },
    )
    assert too_early.status_code == 422
    at_boundary = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T10:35:00Z",
            "end": "2026-12-01T11:05:00Z",
            "user_id": student_id,
        },
    )
    assert at_boundary.status_code == 201, at_boundary.text

    later = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-02T11:04:00Z",
            "end": "2026-12-02T11:34:00Z",
            "user_id": student_id,
        },
    )
    assert later.status_code == 201, later.text
    candidate_break_conflict = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-02T10:30:00Z",
            "end": "2026-12-02T11:00:00Z",
            "user_id": student_id,
        },
    )
    assert candidate_break_conflict.status_code == 422

    final_day = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2027-01-26T10:04:00Z",
            "end": "2027-01-26T10:34:00Z",
            "user_id": student_id,
        },
    )
    assert final_day.status_code == 201, final_day.text
    full_series_conflict = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2027-01-05T09:30:00Z",
            "end": "2027-01-05T10:00:00Z",
            "interval": 7,
            "interval_end": "2027-01-27T09:30:00Z",
            "user_id": student_id,
        },
    )
    assert full_series_conflict.status_code == 422


async def test_vacation_only_overlapping_reserved_break_does_not_block_lesson(
    client: AsyncClient,
) -> None:
    _, teacher_token, students = await _teacher_and_students(
        client, "vacation-auto-break", break_minutes=5
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)
    vacation = await client.post(
        "/api/v1/vacations/",
        headers=headers,
        json={
            "start": "2026-12-01T10:02:00Z",
            "end": "2026-12-01T10:04:00Z",
            "user_id": student_id,
        },
    )
    assert vacation.status_code == 201, vacation.text

    lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T09:30:00Z",
            "end": "2026-12-01T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert lesson.status_code == 201, lesson.text


async def test_automatic_breaks_are_not_managed_through_break_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, teacher_token, students = await _teacher_and_students(
        client, "auto-break-read-only", break_minutes=5
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)
    lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T10:00:00Z",
            "user_id": student_id,
        },
    )
    series = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T11:00:00Z",
            "end": "2026-12-01T12:00:00Z",
            "interval": 7,
            "interval_end": "2026-12-16T11:00:00Z",
            "user_id": student_id,
        },
    )
    assert lesson.status_code == 201, lesson.text
    assert series.status_code == 201, series.text
    lesson_id = UUID(lesson.json()["id"])
    series_id = UUID(series.json()["id"])
    read_only_detail = (
        "Automatic breaks are derived and cannot be edited or deleted independently; "
        "manage the parent lesson instead"
    )

    for response in (
        await client.get(f"/api/v1/breaks/{lesson_id}", headers=headers),
        await client.put(
            f"/api/v1/breaks/{lesson_id}",
            headers=headers,
            json={
                "start": "2026-12-01T09:00:00Z",
                "end": "2026-12-01T10:00:00Z",
                "user_id": teacher_id,
            },
        ),
        await client.delete(f"/api/v1/breaks/{lesson_id}", headers=headers),
        await client.put(
            f"/api/v1/recurrent-breaks/{series_id}",
            headers=headers,
            json={
                "start": "2026-12-01T11:00:00Z",
                "end": "2026-12-01T12:00:00Z",
                "interval": 7,
                "interval_end": "2026-12-16T11:00:00Z",
                "user_id": teacher_id,
            },
        ),
        await client.delete(f"/api/v1/recurrent-breaks/{series_id}", headers=headers),
    ):
        assert response.status_code == 422
        assert response.json()["detail"] == read_only_detail

    student_headers = _headers(students[0][1])
    assert (
        await client.get(f"/api/v1/breaks/{lesson_id}", headers=student_headers)
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/breaks/{lesson_id}", headers=student_headers)
    ).status_code == 403

    _, other_teacher_token, _ = await _teacher_and_students(
        client, "auto-break-other-teacher"
    )
    other_teacher_read = await client.get(
        f"/api/v1/breaks/{lesson_id}",
        headers=_headers(other_teacher_token),
    )
    other_teacher_delete = await client.delete(
        f"/api/v1/breaks/{lesson_id}",
        headers=_headers(other_teacher_token),
    )
    assert other_teacher_read.status_code == 422
    assert other_teacher_read.json()["detail"] == "Break not found"
    assert other_teacher_delete.status_code == 422
    assert other_teacher_delete.json()["detail"] == "Break not found"

    assert await db_session.get(Event, lesson_id) is not None
    assert await db_session.get(RecurrentEvent, series_id) is not None


async def test_break_preference_updates_apply_only_to_future_lesson_snapshots(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, teacher_token, students = await _teacher_and_students(
        client, "auto-break-preference-snapshots", break_minutes=5
    )
    student_id = students[0][0]
    headers = _headers(teacher_token)
    profile_url = f"/api/v1/auth/users/{teacher_id}/teacher-profile"

    existing_lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T10:00:00Z",
            "user_id": student_id,
        },
    )
    existing_series = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-03T11:00:00Z",
            "end": "2026-12-03T12:00:00Z",
            "interval": 7,
            "interval_end": "2026-12-17T11:00:00Z",
            "user_id": student_id,
        },
    )
    assert existing_lesson.status_code == 201, existing_lesson.text
    assert existing_series.status_code == 201, existing_series.text

    profile = await client.get(profile_url, headers=headers)
    assert profile.status_code == 200, profile.text
    original_preferences = profile.json()
    zero_break = await client.put(
        profile_url,
        headers=headers,
        json={"between_lessons_break": 0},
    )
    assert zero_break.status_code == 200, zero_break.text
    assert zero_break.json()["between_lessons_break"] == 0
    assert {
        key: value
        for key, value in zero_break.json().items()
        if key != "between_lessons_break"
    } == {
        key: value
        for key, value in original_preferences.items()
        if key != "between_lessons_break"
    }

    conflicting_lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T10:04:00Z",
            "end": "2026-12-01T11:04:00Z",
            "user_id": student_id,
        },
    )
    assert conflicting_lesson.status_code == 422, conflicting_lesson.text
    unchanged_profile = await client.get(profile_url, headers=headers)
    assert unchanged_profile.status_code == 200, unchanged_profile.text
    assert unchanged_profile.json()["between_lessons_break"] == 0
    assert unchanged_profile.json()["work_start"] == original_preferences["work_start"]
    assert (
        unchanged_profile.json()["max_consecutive_lessons"]
        == original_preferences["max_consecutive_lessons"]
    )

    new_zero_break_lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-02T09:00:00Z",
            "end": "2026-12-02T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert new_zero_break_lesson.status_code == 201, new_zero_break_lesson.text

    nonzero_break = await client.put(
        profile_url,
        headers=headers,
        json={"between_lessons_break": 15},
    )
    assert nonzero_break.status_code == 200, nonzero_break.text
    new_nonzero_break_series = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-04T09:00:00Z",
            "end": "2026-12-04T10:00:00Z",
            "interval": 7,
            "interval_end": "2026-12-18T09:00:00Z",
            "user_id": student_id,
        },
    )
    assert new_nonzero_break_series.status_code == 201, new_nonzero_break_series.text

    existing_lesson_row = await db_session.get(
        Event, UUID(existing_lesson.json()["id"])
    )
    existing_series_row = await db_session.get(
        RecurrentEvent, UUID(existing_series.json()["id"])
    )
    zero_break_row = await db_session.get(
        Event, UUID(new_zero_break_lesson.json()["id"])
    )
    nonzero_break_series_row = await db_session.get(
        RecurrentEvent, UUID(new_nonzero_break_series.json()["id"])
    )
    assert (
        existing_lesson_row is not None and existing_lesson_row.post_break_minutes == 5
    )
    assert (
        existing_series_row is not None and existing_series_row.post_break_minutes == 5
    )
    assert zero_break_row is not None and zero_break_row.post_break_minutes == 0
    assert (
        nonzero_break_series_row is not None
        and nonzero_break_series_row.post_break_minutes == 15
    )


async def test_zero_break_allows_exact_adjacency_and_manual_break_preview_is_safe(
    client: AsyncClient,
) -> None:
    _, teacher_token, students = await _teacher_and_students(
        client, "zero-break-admission"
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
    adjacent = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T09:30:00Z",
            "end": "2026-12-01T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert adjacent.status_code == 201, adjacent.text

    break_teacher_id, break_teacher_token, break_students = await _teacher_and_students(
        client, "manual-auto-break", break_minutes=5
    )
    break_headers = _headers(break_teacher_token)
    break_student_id = break_students[0][0]
    break_lesson = await client.post(
        "/api/v1/lessons/",
        headers=break_headers,
        json={
            "start": "2026-12-02T09:00:00Z",
            "end": "2026-12-02T09:30:00Z",
            "user_id": break_student_id,
        },
    )
    assert break_lesson.status_code == 201, break_lesson.text

    overlapping_parent_and_break = await client.post(
        "/api/v1/breaks/",
        headers=break_headers,
        json={
            "start": "2026-12-02T09:29:00Z",
            "end": "2026-12-02T09:32:00Z",
            "user_id": break_teacher_id,
        },
    )
    assert overlapping_parent_and_break.status_code == 409
    affected = overlapping_parent_and_break.json()["detail"]["affected_occurrences"]
    assert len(affected) == 1
    assert affected[0]["automatic_break"] == {
        "start": "2026-12-02T09:30:00+00:00",
        "end": "2026-12-02T09:35:00+00:00",
        "duration_minutes": 5,
    }

    auto_break_only = await client.post(
        "/api/v1/breaks/",
        headers=break_headers,
        json={
            "start": "2026-12-02T09:32:00Z",
            "end": "2026-12-02T09:34:00Z",
            "user_id": break_teacher_id,
        },
    )
    assert auto_break_only.status_code == 422


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


async def test_recurring_conflicts_reject_midnight_and_handle_different_intervals(
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
    assert overnight.status_code == 422, overnight.text

    first_interval = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-01T10:00:00Z",
            "end": "2026-12-01T10:30:00Z",
            "interval": 2,
            "interval_end": "2026-12-09T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert first_interval.status_code == 201, first_interval.text

    different_interval = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-12-02T10:15:00Z",
            "end": "2026-12-02T10:45:00Z",
            "interval": 3,
            "interval_end": "2026-12-11T10:15:00Z",
            "user_id": student_id,
        },
    )
    assert different_interval.status_code == 422, different_interval.text


async def test_dst_shifted_occurrence_participates_in_conflict_check(
    client: AsyncClient,
    db_session: AsyncSession,  # noqa: ARG001
) -> None:
    _, teacher_token, students = await _teacher_and_students(
        client, "dst-conflict", "Europe/Berlin", "02:00"
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
        client, "break-one-off", break_minutes=5
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
    assert next(item for item in affected if item["source_id"] == str(lesson_id))[
        "automatic_break"
    ] == {
        "start": "2026-12-01T09:30:00+00:00",
        "end": "2026-12-01T09:35:00+00:00",
        "duration_minutes": 5,
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
