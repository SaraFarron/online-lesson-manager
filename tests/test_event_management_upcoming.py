from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.constants import EventType
from backend.events import management as management_service
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from tests.test_event_management_reads import _headers, _teacher_with_students

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=UTC)


def _one_off(
    teacher_id: UUID,
    user_id: UUID,
    start: datetime,
    *,
    event_type: EventType = EventType.LESSON,
    hours: int = 1,
) -> Event:
    return Event(
        teacher_id=teacher_id,
        user_id=user_id,
        event_type=event_type.value,
        start=start,
        end=start + timedelta(hours=hours),
    )


def _series(
    teacher_id: UUID,
    user_id: UUID,
    start: datetime,
    *,
    interval_end: datetime | None,
    event_type: EventType = EventType.LESSON,
    timezone: str | None = None,
) -> RecurrentEvent:
    return RecurrentEvent(
        teacher_id=teacher_id,
        user_id=user_id,
        event_type=event_type.value,
        start=start,
        end=start + timedelta(hours=1),
        interval=7,
        interval_end=interval_end,
        recurrence_mode="UTC_STEP" if timezone is None else "TEACHER_LOCAL",
        recurrence_timezone=timezone,
    )


async def test_upcoming_orders_one_offs_and_series_by_next_occurrence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, _token, student_a, _student_b = await _teacher_with_students(client)
    teacher = await db_session.get(User, UUID(teacher_id))
    assert teacher is not None
    teacher_uuid = teacher.id
    student = UUID(student_a[0])

    past = _one_off(teacher_uuid, student, datetime(2030, 1, 1, 9))
    ongoing = _one_off(teacher_uuid, student, datetime(2030, 1, 10, 11, 30))
    later = _one_off(teacher_uuid, student, datetime(2030, 1, 20, 9))
    open_ended = _series(
        teacher_uuid, student, datetime(2030, 1, 1, 9), interval_end=None
    )
    cancelled_next = _series(
        teacher_uuid, student, datetime(2030, 1, 2, 9), interval_end=None
    )
    ended = _series(
        teacher_uuid,
        student,
        datetime(2029, 12, 1, 9),
        interval_end=datetime(2030, 1, 5, 9),
    )
    fully_cancelled = _series(
        teacher_uuid,
        student,
        datetime(2030, 1, 8, 9),
        interval_end=datetime(2030, 1, 23, 9),
    )
    # Started two years ago: the one-year validation bound must not hide it.
    old_local = _series(
        teacher_uuid,
        student,
        datetime(2030, 1, 14, 9) - timedelta(days=700),
        interval_end=None,
        timezone="Europe/Berlin",
    )
    future_cancelled = _series(
        teacher_uuid, student, datetime(2030, 2, 1, 9), interval_end=None
    )
    future = _series(teacher_uuid, student, datetime(2031, 3, 1, 9), interval_end=None)
    db_session.add_all(
        [past, ongoing, later, open_ended, cancelled_next, ended]
        + [fully_cancelled, old_local, future_cancelled, future]
    )
    await db_session.flush()
    db_session.add_all(
        [
            RecurrentCancels(
                recurrent_event_id=future_cancelled.id,
                start=datetime(2030, 2, 1, 9),
                end=datetime(2030, 2, 1, 10),
            ),
            RecurrentCancels(
                recurrent_event_id=cancelled_next.id,
                start=datetime(2030, 1, 16, 9),
                end=datetime(2030, 1, 16, 10),
            ),
            RecurrentCancels(
                recurrent_event_id=fully_cancelled.id,
                start=datetime(2030, 1, 15, 9),
                end=datetime(2030, 1, 15, 10),
            ),
            RecurrentCancels(
                recurrent_event_id=fully_cancelled.id,
                start=datetime(2030, 1, 22, 9),
                end=datetime(2030, 1, 22, 10),
            ),
        ]
    )
    await db_session.commit()

    page = await management_service.list_upcoming_managed_events(
        db_session,
        teacher,
        EventType.LESSON,
        skip=0,
        limit=100,
        subject_id=None,
        now=NOW,
    )

    assert [(item.id, item.next_occurrence_start) for item in page.items] == [
        (ongoing.id, datetime(2030, 1, 10, 11, 30, tzinfo=UTC)),
        (old_local.id, datetime(2030, 1, 14, 9, tzinfo=UTC)),
        (open_ended.id, datetime(2030, 1, 15, 9, tzinfo=UTC)),
        (later.id, datetime(2030, 1, 20, 9, tzinfo=UTC)),
        (cancelled_next.id, datetime(2030, 1, 23, 9, tzinfo=UTC)),
        (future_cancelled.id, datetime(2030, 2, 8, 9, tzinfo=UTC)),
        (future.id, datetime(2031, 3, 1, 9, tzinfo=UTC)),
    ]
    assert page.total == 7
    assert {item.id: item.cancelled_occurrences for item in page.items}[
        cancelled_next.id
    ] == 1
    assert [item.is_recurrent for item in page.items] == [
        False,
        True,
        True,
        False,
        True,
        True,
        True,
    ]

    second = await management_service.list_upcoming_managed_events(
        db_session, teacher, EventType.LESSON, skip=1, limit=2, subject_id=None, now=NOW
    )
    assert [item.id for item in second.items] == [old_local.id, open_ended.id]
    assert second.total == 7

    breaks = await management_service.list_upcoming_managed_events(
        db_session,
        teacher,
        EventType.BREAK,
        skip=0,
        limit=100,
        subject_id=None,
        now=NOW,
    )
    assert breaks.items == [] and breaks.total == 0


async def test_upcoming_routes_scope_by_role_and_vacation_visibility(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, teacher_token, student_a, student_b = await _teacher_with_students(
        client
    )
    teacher_uuid = UUID(teacher_id)
    student_a_id = UUID(student_a[0])
    student_b_id = UUID(student_b[0])
    base = datetime.now(UTC).replace(tzinfo=None, microsecond=0, second=0) + timedelta(
        days=3
    )
    hidden = _one_off(teacher_uuid, student_a_id, base)
    other = _one_off(teacher_uuid, student_b_id, base + timedelta(days=1))
    weekly = _series(
        teacher_uuid, student_a_id, base + timedelta(days=2), interval_end=None
    )
    manual_break = _one_off(
        teacher_uuid,
        teacher_uuid,
        base + timedelta(hours=5),
        event_type=EventType.BREAK,
    )
    vacation = _one_off(
        teacher_uuid,
        student_a_id,
        base - timedelta(minutes=30),
        event_type=EventType.VACATION,
        hours=2,
    )
    db_session.add_all([hidden, other, weekly, manual_break, vacation])
    await db_session.commit()

    teacher_page = await client.get(
        "/api/v1/lessons/upcoming", headers=_headers(teacher_token)
    )
    assert teacher_page.status_code == 200, teacher_page.text
    assert [item["id"] for item in teacher_page.json()["items"]] == [
        str(hidden.id),
        str(other.id),
        str(weekly.id),
    ]
    assert teacher_page.json()["items"][0]["visibility_status"] == "hidden_by_vacation"
    assert teacher_page.json()["items"][2]["next_occurrence_start"].endswith("+00:00")

    filtered = await client.get(
        "/api/v1/lessons/upcoming",
        params={"subject_id": str(student_b_id)},
        headers=_headers(teacher_token),
    )
    assert [item["id"] for item in filtered.json()["items"]] == [str(other.id)]

    student_page = await client.get(
        "/api/v1/lessons/upcoming", headers=_headers(student_a[1])
    )
    assert student_page.status_code == 200, student_page.text
    assert [item["id"] for item in student_page.json()["items"]] == [
        str(hidden.id),
        str(weekly.id),
    ]
    assert str(other.id) not in student_page.text

    forbidden = await client.get(
        "/api/v1/lessons/upcoming",
        params={"subject_id": str(student_b_id)},
        headers=_headers(student_a[1]),
    )
    assert forbidden.status_code == 403

    break_page = await client.get(
        "/api/v1/breaks/upcoming", headers=_headers(teacher_token)
    )
    assert break_page.status_code == 200, break_page.text
    assert [item["id"] for item in break_page.json()["items"]] == [str(manual_break.id)]
    student_breaks = await client.get(
        "/api/v1/breaks/upcoming", headers=_headers(student_a[1])
    )
    assert student_breaks.status_code == 403

    unauthenticated = await client.get("/api/v1/lessons/upcoming")
    assert unauthenticated.status_code == 401


async def test_upcoming_vacations_hide_finished_and_keep_role_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, teacher_token, student_a, student_b = await _teacher_with_students(
        client
    )
    teacher_uuid = UUID(teacher_id)
    student_a_id = UUID(student_a[0])
    student_b_id = UUID(student_b[0])
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    finished = _one_off(
        teacher_uuid,
        student_a_id,
        now - timedelta(days=3),
        event_type=EventType.VACATION,
        hours=24,
    )
    just_ended = _one_off(
        teacher_uuid,
        student_a_id,
        now - timedelta(hours=2),
        event_type=EventType.VACATION,
    )
    ongoing = _one_off(
        teacher_uuid,
        student_a_id,
        now - timedelta(hours=1),
        event_type=EventType.VACATION,
        hours=48,
    )
    future = _one_off(
        teacher_uuid,
        student_b_id,
        now + timedelta(days=10),
        event_type=EventType.VACATION,
        hours=24,
    )
    personal = _one_off(
        teacher_uuid,
        teacher_uuid,
        now + timedelta(days=5),
        event_type=EventType.VACATION,
        hours=24,
    )
    db_session.add_all([finished, just_ended, ongoing, future, personal])
    await db_session.commit()

    teacher_page = await client.get(
        "/api/v1/vacations/upcoming", headers=_headers(teacher_token)
    )
    assert teacher_page.status_code == 200, teacher_page.text
    assert [item["id"] for item in teacher_page.json()["items"]] == [
        str(ongoing.id),
        str(personal.id),
        str(future.id),
    ]
    assert teacher_page.json()["total"] == 3

    paged = await client.get(
        "/api/v1/vacations/upcoming",
        params={"skip": 1, "limit": 1},
        headers=_headers(teacher_token),
    )
    assert [item["id"] for item in paged.json()["items"]] == [str(personal.id)]
    assert paged.json()["total"] == 3

    filtered = await client.get(
        "/api/v1/vacations/upcoming",
        params={"subject_id": str(student_b_id)},
        headers=_headers(teacher_token),
    )
    assert [item["id"] for item in filtered.json()["items"]] == [str(future.id)]

    student_page = await client.get(
        "/api/v1/vacations/upcoming", headers=_headers(student_a[1])
    )
    assert student_page.status_code == 200, student_page.text
    assert [item["id"] for item in student_page.json()["items"]] == [str(ongoing.id)]

    forbidden = await client.get(
        "/api/v1/vacations/upcoming",
        params={"subject_id": str(student_b_id)},
        headers=_headers(student_a[1]),
    )
    assert forbidden.status_code == 403

    full_list = await client.get("/api/v1/vacations/", headers=_headers(teacher_token))
    assert full_list.json()["total"] == 5

    detail = await client.get(
        f"/api/v1/vacations/{finished.id}", headers=_headers(teacher_token)
    )
    assert detail.status_code == 200

    unauthenticated = await client.get("/api/v1/vacations/upcoming")
    assert unauthenticated.status_code == 401
