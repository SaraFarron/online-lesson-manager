from datetime import datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.lessons.schemas import LessonCreate


async def _register_teacher(client: AsyncClient) -> tuple[str, str]:
    email = "utc-scheduling@test.com"
    register_response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": "TEACHER",
            "timezone": "Asia/Bangkok",
        },
    )
    assert register_response.status_code == 201, register_response.text
    teacher_id = register_response.json()["id"]

    login_response = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert login_response.status_code == 200, login_response.text
    return teacher_id, f"Bearer {login_response.json()['access_token']}"


def test_timestamp_openapi_describes_utc_input() -> None:
    timestamp = LessonCreate.model_json_schema()["properties"]["start"]

    assert timestamp["format"] == "date-time"
    assert "Naive timestamps are interpreted as UTC" in timestamp["description"]
    assert "2026-12-01T16:00:00+07:00" in timestamp["examples"]


async def test_event_apis_normalize_to_utc_and_conflict_on_same_instant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, token = await _register_teacher(client)
    headers = {"Authorization": token}

    lesson_response = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-10-01T16:00:00+07:00",
            "end": "2026-10-01T16:30:00+07:00",
            "user_id": teacher_id,
            "event_type": "LESSON",
        },
    )
    assert lesson_response.status_code == 201, lesson_response.text
    assert lesson_response.json()["start"] == "2026-10-01T09:00:00+00:00"
    assert lesson_response.json()["end"] == "2026-10-01T09:30:00+00:00"

    equivalent_lesson = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-10-01T09:00:00Z",
            "end": "2026-10-01T09:30:00Z",
            "user_id": teacher_id,
            "event_type": "LESSON",
        },
    )
    assert equivalent_lesson.status_code == 422

    break_response = await client.post(
        "/api/v1/breaks/",
        headers=headers,
        json={
            "start": "2026-10-02T09:00:00",
            "end": "2026-10-02T09:30:00",
            "user_id": teacher_id,
        },
    )
    assert break_response.status_code == 201, break_response.text
    assert break_response.json()["start"] == "2026-10-02T09:00:00+00:00"

    vacation_response = await client.post(
        "/api/v1/vacations/",
        headers=headers,
        json={
            "start": "2026-10-03T11:00:00+02:00",
            "end": "2026-10-03T12:00:00+02:00",
            "user_id": teacher_id,
        },
    )
    assert vacation_response.status_code == 201, vacation_response.text
    assert vacation_response.json()["start"] == "2026-10-03T09:00:00+00:00"

    invalid_range = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-10-04T10:00:00",
            "end": "2026-10-04T09:00:00",
            "user_id": teacher_id,
            "event_type": "LESSON",
        },
    )
    assert invalid_range.status_code == 422

    one_minute_event = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-10-04T09:00:00",
            "end": "2026-10-04T09:01:00",
            "user_id": teacher_id,
            "event_type": "LESSON",
        },
    )
    assert one_minute_event.status_code == 422

    events = list((await db_session.scalars(select(Event).order_by(Event.start))).all())
    assert len(events) == 3
    assert [(event.start, event.end) for event in events] == [
        (datetime(2026, 10, 1, 9), datetime(2026, 10, 1, 9, 30)),
        (datetime(2026, 10, 2, 9), datetime(2026, 10, 2, 9, 30)),
        (datetime(2026, 10, 3, 9), datetime(2026, 10, 3, 10)),
    ]


async def test_recurrent_api_snapshots_teacher_timezone_and_normalizes_cancels(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, token = await _register_teacher(client)
    headers = {"Authorization": token}

    one_off_response = await client.post(
        "/api/v1/lessons/",
        headers=headers,
        json={
            "start": "2026-10-19T16:00:00+07:00",
            "end": "2026-10-19T16:30:00+07:00",
            "user_id": teacher_id,
            "event_type": "LESSON",
        },
    )
    assert one_off_response.status_code == 201, one_off_response.text
    one_off_id = UUID(one_off_response.json()["id"])

    invalid_recurrence = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-10-20T16:00:00+07:00",
            "end": "2026-10-20T17:00:00+07:00",
            "interval": 0,
            "interval_end": "2026-10-20T16:00:00+07:00",
            "user_id": teacher_id,
            "event_type": "LESSON",
        },
    )
    assert invalid_recurrence.status_code == 422

    cutoff_at_start = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-10-20T16:00:00+07:00",
            "end": "2026-10-20T17:00:00+07:00",
            "interval": 7,
            "interval_end": "2026-10-20T16:00:00+07:00",
            "user_id": teacher_id,
            "event_type": "LESSON",
        },
    )
    assert cutoff_at_start.status_code == 422

    create_response = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=headers,
        json={
            "start": "2026-10-20T16:00:00+07:00",
            "end": "2026-10-20T17:00:00+07:00",
            "interval": 7,
            "interval_end": "2026-12-01T16:00:00+07:00",
            "user_id": teacher_id,
            "event_type": "LESSON",
        },
    )
    assert create_response.status_code == 201, create_response.text
    response_data = create_response.json()
    assert response_data["start"] == "2026-10-20T09:00:00+00:00"
    assert response_data["end"] == "2026-10-20T10:00:00+00:00"
    assert response_data["interval_end"] == "2026-12-01T09:00:00+00:00"

    event_id = UUID(response_data["id"])
    recurrent_event = await db_session.get(RecurrentEvent, event_id)
    assert recurrent_event is not None
    assert recurrent_event.start == datetime(2026, 10, 20, 9)
    assert recurrent_event.end == datetime(2026, 10, 20, 10)
    assert recurrent_event.interval_end == datetime(2026, 12, 1, 9)
    assert recurrent_event.recurrence_mode == RecurrenceMode.TEACHER_LOCAL.value
    assert recurrent_event.recurrence_timezone == "Asia/Bangkok"

    timezone_response = await client.patch(
        "/api/v1/auth/me", headers=headers, json={"timezone": "Europe/Berlin"}
    )
    assert timezone_response.status_code == 200, timezone_response.text
    one_off = await db_session.get(Event, one_off_id)
    assert one_off is not None
    assert one_off.start == datetime(2026, 10, 19, 9)

    update_response = await client.put(
        f"/api/v1/recurrent-lessons/{event_id}",
        headers=headers,
        json={
            "start": "2026-10-20T09:00:00Z",
            "end": "2026-10-20T10:00:00Z",
            "interval": 7,
            "interval_end": "2026-12-01T09:00:00Z",
            "user_id": teacher_id,
            "cancels": [
                {
                    "start": "2026-10-20T16:00:00+07:00",
                    "end": "2026-10-20T17:00:00+07:00",
                }
            ],
        },
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["start"] == "2026-10-20T09:00:00+00:00"

    await db_session.refresh(recurrent_event)
    assert recurrent_event.start == datetime(2026, 10, 20, 9)
    assert recurrent_event.recurrence_timezone == "Asia/Bangkok"
    cancel = await db_session.scalar(
        select(RecurrentCancels).where(RecurrentCancels.recurrent_event_id == event_id)
    )
    assert cancel is not None
    assert cancel.start == datetime(2026, 10, 20, 9)
    assert cancel.end == datetime(2026, 10, 20, 10)


async def test_open_ended_lesson_write_waits_for_service_support(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, token = await _register_teacher(client)
    response = await client.post(
        "/api/v1/recurrent-lessons/",
        headers={"Authorization": token},
        json={
            "start": "2026-10-27T09:00:00Z",
            "end": "2026-10-27T10:00:00Z",
            "interval": 7,
            "interval_end": None,
            "user_id": teacher_id,
        },
    )

    assert response.status_code == 422
    assert "not supported by this backend release" in response.text
    assert await db_session.scalar(select(RecurrentEvent.id)) is None
