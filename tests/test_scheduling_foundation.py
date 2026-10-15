from datetime import UTC, datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.occurrences import OccurrenceSourceKind
from backend.events.range_service import load_raw_occurrences
from backend.events.visibility import (
    VacationVisibilityReason,
    filter_vacation_visibility,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(
    client: AsyncClient, email: str, role: str, timezone: str = "UTC"
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


async def _teacher_with_students(
    client: AsyncClient,
) -> tuple[str, str, list[tuple[str, str]]]:
    suffix = "foundation-lifecycle"
    teacher_id, teacher_token = await _register_and_login(
        client, f"{suffix}-teacher@test.com", "TEACHER"
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


async def test_full_scheduling_foundation_lifecycle(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher_id, teacher_token, students = await _teacher_with_students(client)
    student_id, student_token = students[0]
    teacher_headers = _headers(teacher_token)

    one_off = await client.post(
        "/api/v1/lessons/",
        headers=teacher_headers,
        json={
            "start": "2027-01-04T16:00:00+07:00",
            "end": "2027-01-04T16:30:00+07:00",
            "user_id": student_id,
        },
    )
    assert one_off.status_code == 201, one_off.text
    one_off_id = UUID(one_off.json()["id"])
    assert one_off.json()["start"] == "2027-01-04T09:00:00+00:00"
    assert one_off.json()["end"] == "2027-01-04T09:30:00+00:00"

    recurring = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=teacher_headers,
        json={
            "start": "2027-01-05T10:00:00Z",
            "end": "2027-01-05T10:30:00Z",
            "interval": 7,
            "interval_end": "2027-01-26T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert recurring.status_code == 201, recurring.text
    series_id = UUID(recurring.json()["id"])
    cancellation_payload = {
        "start": "2027-01-05T10:00:00Z",
        "end": "2027-01-05T10:30:00Z",
        "interval": 7,
        "interval_end": "2027-01-26T10:00:00Z",
        "user_id": student_id,
        "cancels": [
            {
                "start": "2027-01-12T10:00:00Z",
                "end": "2027-01-12T10:30:00Z",
            }
        ],
    }
    skipped = await client.put(
        f"/api/v1/recurrent-lessons/{series_id}",
        headers=teacher_headers,
        json=cancellation_payload,
    )
    assert skipped.status_code == 200, skipped.text

    later_start = datetime(2027, 1, 10, tzinfo=UTC)
    later_end = datetime(2027, 1, 20, tzinfo=UTC)
    raw_later = await load_raw_occurrences(
        db_session, UUID(teacher_id), later_start, later_end
    )
    later_occurrence = next(
        item
        for item in raw_later
        if item.source.kind == OccurrenceSourceKind.RECURRENT_EVENT
    )
    assert later_occurrence.identity == (
        OccurrenceSourceKind.RECURRENT_EVENT,
        series_id,
        datetime(2027, 1, 19, 10, tzinfo=UTC),
    )
    assert len(raw_later) == 1

    vacation = await client.post(
        "/api/v1/vacations/",
        headers=_headers(student_token),
        json={
            "start": "2027-01-19T10:15:00Z",
            "end": "2027-01-19T10:45:00Z",
            "user_id": student_id,
        },
    )
    assert vacation.status_code == 201, vacation.text
    during_vacation = await load_raw_occurrences(
        db_session, UUID(teacher_id), later_start, later_end
    )
    hidden = filter_vacation_visibility(during_vacation, UUID(teacher_id)).hidden
    hidden_lesson = next(
        item.occurrence
        for item in hidden
        if item.occurrence.source.kind == OccurrenceSourceKind.RECURRENT_EVENT
    )
    hidden_entry = next(
        item
        for item in hidden
        if item.occurrence.source.kind == OccurrenceSourceKind.RECURRENT_EVENT
    )
    assert hidden_lesson.identity == later_occurrence.identity
    assert hidden_entry.reasons == (VacationVisibilityReason.STUDENT_VACATION,)

    vacation_delete = await client.delete(
        f"/api/v1/vacations/{vacation.json()['id']}",
        headers=_headers(student_token),
    )
    assert vacation_delete.status_code == 204
    after_vacation = await load_raw_occurrences(
        db_session, UUID(teacher_id), later_start, later_end
    )
    visible = filter_vacation_visibility(after_vacation, UUID(teacher_id)).visible
    assert any(item.identity == later_occurrence.identity for item in visible)

    break_payload = {
        "start": "2027-01-19T10:00:00Z",
        "end": "2027-01-19T10:30:00Z",
        "user_id": teacher_id,
    }
    preview = await client.post(
        "/api/v1/breaks/", headers=teacher_headers, json=break_payload
    )
    assert preview.status_code == 409
    affected = preview.json()["detail"]["affected_occurrences"]
    assert len(affected) == 1
    assert affected[0]["source_id"] == str(series_id)
    assert affected[0]["start"] == "2027-01-19T10:00:00+00:00"

    confirmed_break = await client.post(
        "/api/v1/breaks/?confirm_displacement=true",
        headers=teacher_headers,
        json=break_payload,
    )
    assert confirmed_break.status_code == 201, confirmed_break.text
    assert (
        await db_session.scalar(
            select(func.count(RecurrentCancels.id)).where(
                RecurrentCancels.recurrent_event_id == series_id
            )
        )
        == 2
    )
    deleted_break = await client.delete(
        f"/api/v1/breaks/{confirmed_break.json()['id']}",
        headers=teacher_headers,
    )
    assert deleted_break.status_code == 204
    after_break = await load_raw_occurrences(
        db_session, UUID(teacher_id), later_start, later_end
    )
    assert after_break == []

    edited = await client.put(
        f"/api/v1/recurrent-lessons/{series_id}",
        headers=teacher_headers,
        json={
            "start": "2027-01-05T10:00:00Z",
            "end": "2027-01-05T10:30:00Z",
            "interval": 14,
            "interval_end": "2027-01-26T10:00:00Z",
            "user_id": student_id,
        },
    )
    assert edited.status_code == 200, edited.text
    edited_row = await db_session.get(RecurrentEvent, series_id)
    assert edited_row is not None
    assert edited_row.interval == 14
    assert edited_row.interval_end == datetime(2027, 1, 26, 10)
    assert (
        await db_session.scalar(
            select(func.count(RecurrentCancels.id)).where(
                RecurrentCancels.recurrent_event_id == series_id
            )
        )
        == 0
    )

    conflict = await client.post(
        "/api/v1/lessons/",
        headers=teacher_headers,
        json={
            "start": "2027-01-19T10:15:00Z",
            "end": "2027-01-19T10:45:00Z",
            "user_id": student_id,
        },
    )
    assert conflict.status_code == 422

    recanceled = await client.put(
        f"/api/v1/recurrent-lessons/{series_id}",
        headers=teacher_headers,
        json={
            "start": "2027-01-05T10:00:00Z",
            "end": "2027-01-05T10:30:00Z",
            "interval": 14,
            "interval_end": "2027-01-26T10:00:00Z",
            "user_id": student_id,
            "cancels": [
                {
                    "start": "2027-01-19T10:00:00Z",
                    "end": "2027-01-19T10:30:00Z",
                }
            ],
        },
    )
    assert recanceled.status_code == 200, recanceled.text

    deleted_series = await client.delete(
        f"/api/v1/recurrent-lessons/{series_id}",
        headers=teacher_headers,
    )
    assert deleted_series.status_code == 204
    assert (
        await db_session.scalar(
            select(func.count(RecurrentEvent.id)).where(RecurrentEvent.id == series_id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count(RecurrentCancels.id)).where(
                RecurrentCancels.recurrent_event_id == series_id
            )
        )
        == 0
    )
    assert await db_session.get(Event, one_off_id) is not None
