from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import TeacherProfile, User
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.main import app
from backend.schedule.router import get_schedule_clock
from tests.test_event_management_reads import _headers

MONDAY = date(2030, 1, 7)
SLOTS_URL = "/api/v1/events/available-start-times"
ENDS_URL = "/api/v1/events/available-break-end-times"


async def _register(
    client: AsyncClient, email: str, role: str, timezone: str
) -> tuple[str, str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "full_name": email.split("@")[0].title(),
            "timezone": timezone,
        },
    )
    assert registration.status_code == 201, registration.text
    login = await client.post(
        "/api/v1/auth/login", data={"username": email, "password": "password123"}
    )
    assert login.status_code == 200, login.text
    return registration.json()["id"], login.json()["access_token"]


class World:
    def __init__(
        self,
        teacher: tuple[str, str],
        student_a: tuple[str, str],
        student_b: tuple[str, str],
        db: AsyncSession,
    ) -> None:
        self.teacher_id = UUID(teacher[0])
        self.teacher_token = teacher[1]
        self.a_id = UUID(student_a[0])
        self.a_token = student_a[1]
        self.b_id = UUID(student_b[0])
        self.b_token = student_b[1]
        self.db = db

    def lesson(self, user_id: UUID, start: datetime, minutes: int = 60) -> Event:
        event = Event(
            teacher_id=self.teacher_id,
            user_id=user_id,
            event_type=EventType.LESSON.value,
            start=start,
            end=start + timedelta(minutes=minutes),
            post_break_minutes=5,
        )
        self.db.add(event)
        return event

    async def get(
        self, token: str, url: str = SLOTS_URL, **params: object
    ) -> list[str]:
        response = await self.get_response(token, url, **params)
        assert response.status_code == 200, response.text
        key = "slots" if url == SLOTS_URL else "ends"
        return [item["local"][11:16] for item in response.json()[key]]

    async def get_response(self, token: str, url: str = SLOTS_URL, **params: object):
        return await self._client.get(
            url,
            params={"date": MONDAY.isoformat(), **params},
            headers=_headers(token),
        )

    _client: AsyncClient


async def _world(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    *,
    teacher_timezone: str = "UTC",
    student_timezone: str = "UTC",
    now: datetime = datetime(2030, 1, 7, 8, 0, tzinfo=UTC),
    **profile: object,
) -> World:
    teacher = await _register(client, "avail-teacher@test.com", "TEACHER", "UTC")
    created = await client.post(
        f"/api/v1/auth/users/{teacher[0]}/teacher-profile",
        headers=_headers(teacher[1]),
        json={"code": "t_AVAIL1"},
    )
    assert created.status_code == 201, created.text
    students = []
    for letter in ("a", "b"):
        student = await _register(
            client, f"avail-student-{letter}@test.com", "STUDENT", student_timezone
        )
        linked = await client.post(
            "/api/v1/auth/me/teacher",
            headers=_headers(student[1]),
            json={"teacher_code": "t_AVAIL1"},
        )
        assert linked.status_code == 200, linked.text
        students.append(student)

    teacher_row = await db_session.get(User, UUID(teacher[0]))
    assert teacher_row is not None
    teacher_row.timezone = teacher_timezone
    stored = await db_session.scalar(
        select(TeacherProfile).where(TeacherProfile.teacher_id == UUID(teacher[0]))
    )
    assert stored is not None
    stored.work_start = time(9)
    stored.work_end = time(18)
    stored.working_days = [1, 2, 3, 4, 5]
    stored.lesson_length = 60
    stored.between_lessons_break = 5
    stored.max_consecutive_lessons = 4
    stored.max_lessons_per_day = 6
    for name, value in profile.items():
        setattr(stored, name, value)
    await db_session.commit()
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: now)
    world = World(teacher, students[0], students[1], db_session)
    world._client = client
    return world


def _times(start: str, end: str, step: int) -> list[str]:
    cursor = datetime.combine(MONDAY, time.fromisoformat(start))
    last = datetime.combine(MONDAY, time.fromisoformat(end))
    values = []
    while cursor <= last:
        values.append(cursor.strftime("%H:%M"))
        cursor += timedelta(minutes=step)
    return values


async def test_lesson_slots_exclude_past_conflicts_and_automatic_breaks(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(
        client,
        db_session,
        monkeypatch,
        now=datetime(2030, 1, 7, 10, 30, tzinfo=UTC),
    )
    world.lesson(world.a_id, datetime(2030, 1, 7, 14))
    await db_session.commit()

    slots = await world.get(world.teacher_token, subject_id=str(world.b_id))
    assert slots == _times("10:45", "12:45", 15) + _times("15:15", "17:00", 15)

    response = await world.get_response(world.teacher_token, subject_id=str(world.b_id))
    first = response.json()["slots"][0]
    assert response.json()["timezone"] == "UTC"
    assert response.json()["duration_minutes"] == 60
    assert first["utc"] == "2030-01-07T10:45:00+00:00"
    assert first["utc_offset"] == "+00:00"


async def test_five_minute_grid_and_invalid_steps(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    world.lesson(world.a_id, datetime(2030, 1, 7, 14))
    await db_session.commit()

    slots = await world.get(world.teacher_token, step_minutes=5)
    assert slots == _times("09:00", "12:55", 5) + _times("15:05", "17:00", 5)
    assert all(int(value[-2:]) % 5 == 0 for value in slots)
    for invalid in (1, 10, 30):
        rejected = await world.get_response(world.teacher_token, step_minutes=invalid)
        assert rejected.status_code == 422


async def test_non_working_days_and_hours_have_no_slots(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch, working_days=[2, 3])
    assert await world.get(world.teacher_token) == []

    world.db.expire_all()
    stored = await db_session.scalar(select(TeacherProfile))
    assert stored is not None
    stored.working_days = [1]
    stored.work_start = time(9, 30)
    stored.work_end = time(11)
    await db_session.commit()
    assert await world.get(world.teacher_token) == ["09:30", "09:45", "10:00"]
    assert await world.get(world.teacher_token, duration_minutes=30) == _times(
        "09:30", "10:30", 15
    )


async def test_daily_and_consecutive_limits_are_applied(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch, max_lessons_per_day=1)
    world.lesson(world.a_id, datetime(2030, 1, 7, 12))
    await db_session.commit()
    assert await world.get(world.teacher_token) == []

    stored = await db_session.scalar(select(TeacherProfile))
    assert stored is not None
    stored.max_lessons_per_day = 6
    stored.max_consecutive_lessons = 1
    stored.between_lessons_break = 15
    await db_session.commit()
    slots = await world.get(world.teacher_token)
    # A lesson within the break gap of the existing one would chain two lessons.
    assert "13:15" not in slots
    assert "13:30" in slots
    assert "10:30" in slots
    assert "10:45" not in slots


async def test_vacations_block_only_applicable_bookings(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    for owner, start, hours in (
        (world.a_id, datetime(2030, 1, 7, 11), 2),
        (world.teacher_id, datetime(2030, 1, 7, 16), 1),
    ):
        db_session.add(
            Event(
                teacher_id=world.teacher_id,
                user_id=owner,
                event_type=EventType.VACATION.value,
                start=start,
                end=start + timedelta(hours=hours),
            )
        )
    await db_session.commit()

    for_a = await world.get(world.teacher_token, subject_id=str(world.a_id))
    for_b = await world.get(world.teacher_token, subject_id=str(world.b_id))
    assert "10:15" not in for_a and "12:45" not in for_a and "13:00" in for_a
    assert "11:00" in for_b and "12:45" in for_b
    assert "15:15" not in for_a and "15:15" not in for_b
    assert "17:00" in for_a


async def test_exclude_event_allows_current_time_when_editing_or_moving(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    lesson = world.lesson(world.a_id, datetime(2030, 1, 7, 14))
    series = RecurrentEvent(
        teacher_id=world.teacher_id,
        user_id=world.a_id,
        event_type=EventType.LESSON.value,
        start=datetime(2030, 1, 7, 10),
        end=datetime(2030, 1, 7, 11),
        interval=7,
        interval_end=None,
        recurrence_mode="TEACHER_LOCAL",
        recurrence_timezone="UTC",
        post_break_minutes=5,
    )
    db_session.add(series)
    await db_session.commit()

    assert "14:00" not in await world.get(world.teacher_token)
    assert "14:00" in await world.get(
        world.teacher_token, exclude_event_id=str(lesson.id)
    )
    assert "10:00" not in await world.get(world.teacher_token)
    assert "10:00" in await world.get(
        world.teacher_token, exclude_event_id=str(series.id)
    )
    moved = await world.get(
        world.teacher_token,
        exclude_event_id=str(series.id),
        exclude_occurrence_start="2030-01-07T10:00:00Z",
    )
    assert "10:00" in moved
    other_occurrence = await world.get(
        world.teacher_token,
        exclude_event_id=str(series.id),
        exclude_occurrence_start="2030-01-14T10:00:00Z",
    )
    assert "10:00" not in other_occurrence
    db_session.add(
        RecurrentCancels(
            recurrent_event_id=series.id,
            start=datetime(2030, 1, 7, 10),
            end=datetime(2030, 1, 7, 11),
        )
    )
    await db_session.commit()
    assert "10:00" in await world.get(world.teacher_token)


async def test_exclude_event_requires_ownership(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    lesson = world.lesson(world.a_id, datetime(2030, 1, 7, 14))
    await db_session.commit()

    foreign = await world.get_response(world.b_token, exclude_event_id=str(lesson.id))
    assert foreign.status_code == 403
    missing = await world.get_response(
        world.b_token, exclude_event_id="00000000-0000-0000-0000-000000000001"
    )
    assert missing.status_code == 422
    own = await world.get_response(world.a_token, exclude_event_id=str(lesson.id))
    assert own.status_code == 200
    assert "14:00" in [item["local"][11:16] for item in own.json()["slots"]]


async def test_student_gets_own_booking_slots_without_other_event_details(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    world.lesson(world.b_id, datetime(2030, 1, 7, 14))
    await db_session.commit()

    response = await world.get_response(world.a_token)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {
        "timezone",
        "day",
        "step_minutes",
        "duration_minutes",
        "slots",
    }
    assert all(set(slot) == {"local", "utc", "utc_offset"} for slot in payload["slots"])
    assert "13:00" not in [slot["local"][11:16] for slot in payload["slots"]]
    assert "09:00" in [slot["local"][11:16] for slot in payload["slots"]]

    assert (
        await world.get_response(world.a_token, subject_id=str(world.b_id))
    ).status_code == 403
    assert (
        await world.get_response(world.a_token, duration_minutes=30)
    ).status_code == 422
    assert (
        await world.get_response(world.a_token, duration_minutes=60)
    ).status_code == 200
    assert (
        await world.get_response(world.a_token, event_type="BREAK")
    ).status_code == 403
    assert (
        await world.get_response(world.a_token, event_type="VACATION")
    ).status_code == 422


async def test_teacher_authorization_boundaries(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    stranger = await _register(client, "avail-stranger@test.com", "STUDENT", "UTC")
    unauthenticated = await client.get(SLOTS_URL, params={"date": MONDAY.isoformat()})
    assert unauthenticated.status_code == 401
    assert (
        await world.get_response(world.teacher_token, subject_id=stranger[0])
    ).status_code == 403
    teacher_break_for_student = await world.get_response(
        world.teacher_token, event_type="BREAK", subject_id=str(world.a_id)
    )
    assert teacher_break_for_student.status_code == 422


async def test_student_in_another_timezone_sees_teacher_work_hours_only(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(
        client,
        db_session,
        monkeypatch,
        student_timezone="Asia/Bangkok",
        now=datetime(2030, 1, 6, tzinfo=UTC),
    )
    response = await world.get_response(world.a_token)
    assert response.status_code == 200
    slots = response.json()["slots"]
    assert slots[0]["local"].startswith("2030-01-07T16:00")
    assert slots[0]["utc"] == "2030-01-07T09:00:00+00:00"
    assert slots[-1]["local"].startswith("2030-01-07T23:45")
    assert slots[-1]["utc"] == "2030-01-07T16:45:00+00:00"


async def test_past_times_are_not_offered(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(
        client,
        db_session,
        monkeypatch,
        now=datetime(2030, 1, 7, 17, 30, tzinfo=UTC),
    )
    assert await world.get(world.teacher_token) == []
    world_slots = await world.get(world.teacher_token, duration_minutes=15)
    assert world_slots == ["17:45"]


async def test_dst_gap_and_fold_days(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(
        client,
        db_session,
        monkeypatch,
        teacher_timezone="America/New_York",
        now=datetime(2030, 1, 1, tzinfo=UTC),
        working_days=[1, 2, 3, 4, 5, 6, 7],
        work_start=time(0),
        work_end=time(23, 59),
        between_lessons_break=0,
    )
    gap = await world.get_response(
        world.teacher_token, date="2030-03-10", duration_minutes=30
    )
    assert gap.status_code == 200, gap.text
    gap_slots = [slot["local"] for slot in gap.json()["slots"]]
    assert not any(value[11:13] == "02" for value in gap_slots)
    assert "2030-03-10T01:45:00-05:00" in gap_slots
    assert "2030-03-10T03:00:00-04:00" in gap_slots

    fold = await world.get_response(
        world.teacher_token, date="2030-11-03", duration_minutes=30
    )
    fold_slots = fold.json()["slots"]
    ambiguous = [slot for slot in fold_slots if slot["local"][11:16] == "01:30"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["utc_offset"] == "-04:00"
    assert ambiguous[0]["utc"] == "2030-11-03T05:30:00+00:00"
    assert len({slot["utc"] for slot in fold_slots}) == len(fold_slots)


async def test_offered_lesson_slots_match_the_create_endpoint(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(
        client,
        db_session,
        monkeypatch,
        now=datetime(2030, 1, 7, 8, 0, tzinfo=UTC),
        max_consecutive_lessons=2,
    )
    world.lesson(world.a_id, datetime(2030, 1, 7, 11))
    world.lesson(world.a_id, datetime(2030, 1, 7, 12, 5))
    db_session.add(
        Event(
            teacher_id=world.teacher_id,
            user_id=world.b_id,
            event_type=EventType.VACATION.value,
            start=datetime(2030, 1, 7, 16),
            end=datetime(2030, 1, 7, 17),
        )
    )
    await db_session.commit()

    for step in (15, 5):
        offered = set(
            await world.get(
                world.teacher_token, subject_id=str(world.b_id), step_minutes=step
            )
        )
        probes = (
            _times("08:00", "19:00", 15)
            if step == 15
            else ["10:05", "10:55", "11:05", "12:50", "13:05", "13:10", "15:55"]
        )
        assert offered, "the representative state must offer slots"
        for probe in probes:
            start = datetime.combine(MONDAY, time.fromisoformat(probe), tzinfo=UTC)
            created = await client.post(
                "/api/v1/lessons/",
                headers=_headers(world.teacher_token),
                json={"start": start.isoformat(), "user_id": str(world.b_id)},
            )
            accepted = created.status_code == 201
            assert accepted == (probe in offered), (
                f"{probe}: create returned {created.status_code} {created.text}"
            )
            if accepted:
                deleted = await client.delete(
                    f"/api/v1/lessons/{created.json()['id']}",
                    headers=_headers(world.teacher_token),
                )
                assert deleted.status_code == 204


async def test_student_offered_slots_are_accepted_by_student_create(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    world.lesson(world.b_id, datetime(2030, 1, 7, 13))
    await db_session.commit()
    offered = set(await world.get(world.a_token))
    for probe in (
        "09:00",
        "11:45",
        "12:00",
        "12:45",
        "13:00",
        "14:00",
        "17:00",
        "17:15",
    ):
        start = datetime.combine(MONDAY, time.fromisoformat(probe), tzinfo=UTC)
        created = await client.post(
            "/api/v1/lessons/",
            headers=_headers(world.a_token),
            json={"start": start.isoformat(), "user_id": str(world.a_id)},
        )
        assert (created.status_code == 201) == (probe in offered), probe
        if created.status_code == 201:
            await client.delete(
                f"/api/v1/lessons/{created.json()['id']}",
                headers=_headers(world.a_token),
            )


async def test_break_starts_and_ends_stay_in_work_hours_and_never_overlap(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    world.lesson(world.a_id, datetime(2030, 1, 7, 11))
    db_session.add(
        Event(
            teacher_id=world.teacher_id,
            user_id=world.teacher_id,
            event_type=EventType.BREAK.value,
            start=datetime(2030, 1, 7, 15),
            end=datetime(2030, 1, 7, 15, 30),
        )
    )
    await db_session.commit()

    starts = await world.get(world.teacher_token, event_type="BREAK")
    assert starts[0] == "09:00"
    assert starts[-1] == "17:45"
    assert "10:45" in starts
    # 11:00 lesson, 12:00 lesson end plus its five-minute automatic break.
    assert not set(_times("11:00", "12:00", 15)) & set(starts)
    assert "12:15" in starts and "12:00" not in starts
    assert "14:45" in starts and "15:00" not in starts and "15:15" not in starts
    assert "15:30" in starts

    ends = await world.get(world.teacher_token, ENDS_URL, start="2030-01-07T09:00:00Z")
    assert ends == _times("09:15", "11:00", 15)
    ends = await world.get(world.teacher_token, ENDS_URL, start="2030-01-07T12:15:00Z")
    assert ends == _times("12:30", "15:00", 15)
    ends = await world.get(world.teacher_token, ENDS_URL, start="2030-01-07T15:30:00Z")
    assert ends == _times("15:45", "18:00", 15)
    blocked = await world.get(
        world.teacher_token, ENDS_URL, start="2030-01-07T11:30:00Z"
    )
    assert blocked == []
    fine = await world.get(
        world.teacher_token, ENDS_URL, start="2030-01-07T09:05:00Z", step_minutes=5
    )
    assert fine[0] == "09:10" and fine[-1] == "11:00"
    # Ends follow the clock grid, so an off-grid start still gets aligned ends.
    assert await world.get(
        world.teacher_token, ENDS_URL, start="2030-01-07T09:05:00Z"
    ) == _times("09:15", "11:00", 15)
    invalid_step = await world.get_response(
        world.teacher_token, ENDS_URL, start="2030-01-07T09:00:00Z", step_minutes=10
    )
    assert invalid_step.status_code == 422
    student = await world.get_response(
        world.a_token, ENDS_URL, start="2030-01-07T09:00:00Z"
    )
    assert student.status_code == 403


async def test_offered_break_pairs_create_without_displacement_preview(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(client, db_session, monkeypatch)
    world.lesson(world.a_id, datetime(2030, 1, 7, 11))
    await db_session.commit()

    starts = await world.get(world.teacher_token, event_type="BREAK")
    for start_text in ("09:00", "10:45", "12:15", "17:45"):
        assert start_text in starts
        ends = await world.get(
            world.teacher_token,
            ENDS_URL,
            start=f"2030-01-07T{start_text}:00Z",
        )
        assert ends
        for end_text in (ends[0], ends[-1]):
            created = await client.post(
                "/api/v1/breaks/",
                headers=_headers(world.teacher_token),
                json={
                    "start": f"2030-01-07T{start_text}:00Z",
                    "end": f"2030-01-07T{end_text}:00Z",
                    "user_id": str(world.teacher_id),
                    "event_type": "BREAK",
                },
            )
            assert created.status_code == 201, (start_text, end_text, created.text)
            deleted = await client.delete(
                f"/api/v1/breaks/{created.json()['id']}",
                headers=_headers(world.teacher_token),
            )
            assert deleted.status_code == 204, deleted.text
    # A rejected start would have produced a displacement preview on write.
    overlapping = await client.post(
        "/api/v1/breaks/",
        headers=_headers(world.teacher_token),
        json={
            "start": "2030-01-07T11:30:00Z",
            "end": "2030-01-07T11:45:00Z",
            "user_id": str(world.teacher_id),
            "event_type": "BREAK",
        },
    )
    assert overlapping.status_code == 409
    assert "11:30" not in starts
