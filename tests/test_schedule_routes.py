from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.auth.utils import create_access_token
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentEvent
from backend.main import app
from backend.schedule.router import get_schedule_clock


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


async def _user(
    session: AsyncSession,
    role: Roles,
    *,
    timezone: str = "UTC",
    teacher_id: UUID | None = None,
    is_active: bool = True,
) -> User:
    user = User(
        id=uuid4(),
        email=f"schedule-{uuid4()}@example.com",
        role=role.value,
        timezone=timezone,
        teacher_id=teacher_id,
        is_active=is_active,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
def fixed_schedule_clock():
    fixed_time = datetime(2026, 10, 18, 18, 30, tzinfo=UTC)
    app.dependency_overrides[get_schedule_clock] = lambda: fixed_time
    try:
        yield fixed_time
    finally:
        app.dependency_overrides.pop(get_schedule_clock, None)


async def test_schedule_routes_require_valid_active_user_jwt(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, Roles.TEACHER)
    inactive_teacher = await _user(db_session, Roles.TEACHER, is_active=False)
    admin = await _user(db_session, Roles.ADMIN)
    unlinked_student = await _user(db_session, Roles.STUDENT)
    await db_session.flush()
    db_session.add(
        Event(
            teacher_id=teacher.id,
            user_id=teacher.id,
            event_type="LESSON",
            start=datetime(2026, 10, 19, 9),
            end=datetime(2026, 10, 19, 10),
        )
    )
    await db_session.commit()

    missing = await client.get("/api/v1/schedule/day")
    invalid = await client.get(
        "/api/v1/schedule/day", headers={"Authorization": "Bearer invalid"}
    )
    inactive = await client.get(
        "/api/v1/schedule/day", headers=_auth_header(inactive_teacher)
    )
    unsupported_role = await client.get(
        "/api/v1/schedule/day", headers=_auth_header(admin)
    )
    unlinked = await client.get(
        "/api/v1/schedule/day", headers=_auth_header(unlinked_student)
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert inactive.status_code == 400
    assert unsupported_role.status_code == 403
    assert unlinked.status_code == 400
    assert "days" not in missing.text
    assert "days" not in invalid.text
    assert "days" not in inactive.text


async def test_day_default_and_explicit_week_date_use_viewer_timezone(
    client: AsyncClient,
    db_session: AsyncSession,
    fixed_schedule_clock: datetime,
) -> None:
    teacher = await _user(db_session, Roles.TEACHER, timezone="Asia/Bangkok")
    headers = _auth_header(teacher)

    today = await client.get("/api/v1/schedule/day", headers=headers)
    current_week = await client.get("/api/v1/schedule/week", headers=headers)
    week = await client.get("/api/v1/schedule/week?date=2026-10-25", headers=headers)
    invalid = await client.get("/api/v1/schedule/day?date=not-a-date", headers=headers)
    out_of_range = await client.get(
        "/api/v1/schedule/day?date=9999-12-31", headers=headers
    )

    assert today.status_code == 200, today.text
    assert today.json()["days"][0]["date"] == "2026-10-19"
    assert today.json()["generated_at"] == fixed_schedule_clock.isoformat()
    assert current_week.status_code == 200, current_week.text
    assert current_week.json()["days"][0]["date"] == "2026-10-19"
    assert current_week.json()["days"][-1]["date"] == "2026-10-25"
    assert week.status_code == 200, week.text
    assert [entry["date"] for entry in week.json()["days"]] == [
        "2026-10-19",
        "2026-10-20",
        "2026-10-21",
        "2026-10-22",
        "2026-10-23",
        "2026-10-24",
        "2026-10-25",
    ]
    assert week.json()["range_start"] == "2026-10-18T17:00:00+00:00"
    assert week.json()["range_end"] == "2026-10-25T17:00:00+00:00"
    assert invalid.status_code == 422
    assert out_of_range.status_code == 422


async def test_dst_day_and_week_bounds_and_start_date_filter_over_http(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teacher = await _user(db_session, Roles.TEACHER, timezone="America/New_York")
    db_session.add_all(
        [
            Event(
                teacher_id=teacher.id,
                user_id=teacher.id,
                event_type="LESSON",
                start=datetime(2026, 3, 8, 4, 30),
                end=datetime(2026, 3, 8, 7, 30),
            ),
            Event(
                teacher_id=teacher.id,
                user_id=teacher.id,
                event_type="LESSON",
                start=datetime(2026, 3, 8, 6, 30),
                end=datetime(2026, 3, 8, 7, 30),
            ),
        ]
    )
    await db_session.commit()
    headers = _auth_header(teacher)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 3, 8, 7, tzinfo=UTC),
    )

    day = await client.get("/api/v1/schedule/day?date=2026-03-08", headers=headers)
    week = await client.get("/api/v1/schedule/week?date=2026-03-08", headers=headers)

    assert day.status_code == 200, day.text
    assert day.json()["range_start"] == "2026-03-08T05:00:00+00:00"
    assert day.json()["range_end"] == "2026-03-09T04:00:00+00:00"
    assert len(day.json()["days"][0]["items"]) == 1
    assert day.json()["days"][0]["items"][0]["start"] == "2026-03-08T06:30:00+00:00"
    assert week.status_code == 200, week.text
    assert week.json()["range_start"] == "2026-03-02T05:00:00+00:00"
    assert week.json()["range_end"] == "2026-03-09T04:00:00+00:00"
    assert len(week.json()["days"]) == 7


async def test_schedule_route_serializes_automatic_break_parent_reference(
    client: AsyncClient,
    db_session: AsyncSession,
    fixed_schedule_clock: datetime,
) -> None:
    teacher = await _user(db_session, Roles.TEACHER)
    student = await _user(db_session, Roles.STUDENT, teacher_id=teacher.id)
    lesson = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type="LESSON",
        start=datetime(2026, 10, 19, 9),
        end=datetime(2026, 10, 19, 10),
        post_break_minutes=5,
    )
    db_session.add(lesson)
    await db_session.commit()

    teacher_response = await client.get(
        "/api/v1/schedule/day?date=2026-10-19",
        headers=_auth_header(teacher),
    )
    student_response = await client.get(
        "/api/v1/schedule/day?date=2026-10-19",
        headers=_auth_header(student),
    )

    assert teacher_response.status_code == 200, teacher_response.text
    assert student_response.status_code == 200, student_response.text
    for response in (teacher_response, student_response):
        assert response.json()["generated_at"] == fixed_schedule_clock.isoformat()
        automatic_break = next(
            item
            for item in response.json()["days"][0]["items"]
            if item["source_kind"] == "automatic_break"
        )
        assert automatic_break == {
            "source_kind": "automatic_break",
            "source_id": str(lesson.id),
            "event_type": "BREAK",
            "parent_kind": "event",
            "parent_start": "2026-10-19T09:00:00+00:00",
            "start": "2026-10-19T10:00:00+00:00",
            "end": "2026-10-19T10:05:00+00:00",
            "status": "upcoming",
        }


async def test_invalid_recurrence_data_returns_explicit_http_error_not_partial_schedule(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, Roles.TEACHER)
    db_session.add_all(
        [
            Event(
                teacher_id=teacher.id,
                user_id=teacher.id,
                event_type="LESSON",
                start=datetime(2026, 10, 19, 9),
                end=datetime(2026, 10, 19, 10),
            ),
            RecurrentEvent(
                teacher_id=teacher.id,
                user_id=teacher.id,
                event_type="LESSON",
                start=datetime(2026, 10, 19, 11),
                end=datetime(2026, 10, 19, 12),
                interval=7,
                interval_end=datetime(2026, 11, 19, 11),
                recurrence_mode=RecurrenceMode.TEACHER_LOCAL.value,
                recurrence_timezone="Invalid/Timezone",
            ),
        ]
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/schedule/day?date=2026-10-19",
        headers=_auth_header(teacher),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid IANA timezone: Invalid/Timezone"
    assert "days" not in response.json()


def test_schedule_openapi_documents_routes_and_query_date() -> None:
    schema = app.openapi()

    for path in ("/api/v1/schedule/day", "/api/v1/schedule/week"):
        assert path in schema["paths"]
        operation = schema["paths"][path]["get"]
        date_parameter = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "date"
        )
        date_schema = date_parameter["schema"]
        date_variants = date_schema.get("anyOf", [date_schema])
        assert any(variant.get("format") == "date" for variant in date_variants)
        assert operation["summary"]
        assert "200" in operation["responses"]
        assert "400" in operation["responses"]
        assert "401" in operation["responses"]
        assert "403" in operation["responses"]
        assert "409" in operation["responses"]
        assert "422" in operation["responses"]
