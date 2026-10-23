from datetime import UTC, datetime, time, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import TeacherProfile, User
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels
from backend.events.schemas import RecurrentCancel
from backend.events.service import (
    create_event,
    create_recurrent_event,
    update_recurrent_event,
)
from backend.events.validators import EventValidator, default_event_validator
from backend.exceptions import ValidationError
from backend.lessons.schemas import (
    LessonCreate,
    RecurrentLessonCreate,
    RecurrentLessonUpdate,
)


async def _teacher_with_student(
    session: AsyncSession,
    *,
    timezone: str = "Asia/Bangkok",
    work_start: time = time(9),
    work_end: time = time(18),
    working_days: list[int] | None = None,
    lesson_length: int = 60,
    between_lessons_break: int = 5,
    max_consecutive_lessons: int = 4,
    max_lessons_per_day: int = 6,
) -> tuple[User, User]:
    teacher = User(role=Roles.TEACHER, timezone=timezone)
    session.add(teacher)
    await session.flush()
    student = User(role=Roles.STUDENT, timezone="UTC", teacher_id=teacher.id)
    session.add(student)
    session.add(
        TeacherProfile(
            teacher_id=teacher.id,
            code="t_POLICY1",
            work_start=work_start,
            work_end=work_end,
            working_days=working_days or [1, 2, 3, 4, 5],
            lesson_length=lesson_length,
            between_lessons_break=between_lessons_break,
            max_consecutive_lessons=max_consecutive_lessons,
            max_lessons_per_day=max_lessons_per_day,
        )
    )
    await session.commit()
    return teacher, student


@pytest.mark.asyncio
async def test_omitted_lesson_end_uses_configured_duration(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(db_session)
    start = datetime(2026, 10, 26, 9, tzinfo=UTC)

    event = await create_event(
        db_session,
        LessonCreate(start=start, user_id=student.id),
        default_event_validator,
        teacher_id=teacher.id,
    )

    assert event.start == start.replace(tzinfo=None)
    assert event.end == (start + timedelta(minutes=60)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_student_cannot_select_non_default_lesson_duration(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(db_session)

    with pytest.raises(ValidationError, match="configured lesson duration"):
        await create_event(
            db_session,
            LessonCreate(
                start=datetime(2026, 10, 26, 9, tzinfo=UTC),
                end=datetime(2026, 10, 26, 10, 30, tzinfo=UTC),
                user_id=student.id,
            ),
            default_event_validator,
            teacher_id=teacher.id,
        )

    assert await db_session.scalar(select(func.count()).select_from(Event)) == 0


@pytest.mark.asyncio
async def test_teacher_can_select_non_default_duration(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(db_session)
    start = datetime(2026, 10, 26, 9, tzinfo=UTC)

    event = await create_event(
        db_session,
        LessonCreate(
            start=start,
            end=start + timedelta(minutes=75),
            user_id=student.id,
        ),
        default_event_validator,
        teacher_id=teacher.id,
        actor_is_teacher=True,
    )

    assert event.end == (start + timedelta(minutes=75)).replace(tzinfo=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (
            datetime(2026, 10, 26, 8, 59, tzinfo=UTC),
            datetime(2026, 10, 26, 9, 59, tzinfo=UTC),
            "before the teacher's work hours",
        ),
        (
            datetime(2026, 10, 26, 17, 1, tzinfo=UTC),
            datetime(2026, 10, 26, 18, 1, tzinfo=UTC),
            "after the teacher's work hours",
        ),
        (
            datetime(2026, 10, 24, 9, tzinfo=UTC),
            datetime(2026, 10, 24, 10, tzinfo=UTC),
            "non-working day",
        ),
        (
            datetime(2026, 10, 26, 23, 30, tzinfo=UTC),
            datetime(2026, 10, 27, 0, 30, tzinfo=UTC),
            "cross local midnight",
        ),
    ],
)
async def test_lesson_must_fit_teacher_work_day(
    db_session: AsyncSession,
    start: datetime,
    end: datetime,
    message: str,
) -> None:
    teacher, student = await _teacher_with_student(
        db_session,
        timezone="UTC",
        work_start=time(9),
        work_end=time(18),
    )

    with pytest.raises(ValidationError, match=message):
        await create_event(
            db_session,
            LessonCreate(start=start, end=end, user_id=student.id),
            default_event_validator,
            teacher_id=teacher.id,
            actor_is_teacher=True,
        )


@pytest.mark.asyncio
async def test_lesson_ending_exactly_at_work_end_is_allowed(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(db_session, timezone="UTC")

    event = await create_event(
        db_session,
        LessonCreate(
            start=datetime(2026, 10, 26, 17, tzinfo=UTC),
            end=datetime(2026, 10, 26, 18, tzinfo=UTC),
            user_id=student.id,
        ),
        default_event_validator,
        teacher_id=teacher.id,
    )

    assert event.end == datetime(2026, 10, 26, 18)


async def _create_lesson(
    session: AsyncSession, teacher: User, user: User, start: datetime
) -> Event:
    return await create_event(
        session,
        LessonCreate(
            start=start,
            end=start + timedelta(minutes=60),
            user_id=user.id,
        ),
        default_event_validator,
        teacher_id=teacher.id,
        actor_is_teacher=True,
    )


@pytest.mark.asyncio
async def test_daily_limit_counts_teacher_personal_and_student_lessons(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(
        db_session, timezone="UTC", max_lessons_per_day=2
    )
    await _create_lesson(
        db_session, teacher, teacher, datetime(2026, 10, 26, 9, tzinfo=UTC)
    )
    await _create_lesson(
        db_session, teacher, student, datetime(2026, 10, 26, 10, 5, tzinfo=UTC)
    )

    with pytest.raises(ValidationError, match="daily lesson limit of 2"):
        await _create_lesson(
            db_session, teacher, student, datetime(2026, 10, 26, 12, tzinfo=UTC)
        )


@pytest.mark.asyncio
async def test_consecutive_limit_includes_exact_configured_gap(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(
        db_session,
        timezone="UTC",
        max_consecutive_lessons=2,
        between_lessons_break=5,
    )
    await _create_lesson(
        db_session, teacher, student, datetime(2026, 10, 26, 9, tzinfo=UTC)
    )
    await _create_lesson(
        db_session, teacher, student, datetime(2026, 10, 26, 10, 5, tzinfo=UTC)
    )

    with pytest.raises(ValidationError, match="consecutive lesson limit of 2"):
        await _create_lesson(
            db_session, teacher, student, datetime(2026, 10, 26, 11, 10, tzinfo=UTC)
        )

    allowed = await _create_lesson(
        db_session, teacher, student, datetime(2026, 10, 26, 11, 11, tzinfo=UTC)
    )
    assert allowed.start == datetime(2026, 10, 26, 11, 11)


@pytest.mark.asyncio
async def test_zero_break_joins_only_exactly_adjacent_lessons(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(
        db_session,
        timezone="UTC",
        between_lessons_break=0,
        max_consecutive_lessons=1,
    )
    await _create_lesson(
        db_session, teacher, student, datetime(2026, 10, 26, 9, tzinfo=UTC)
    )

    with pytest.raises(ValidationError, match="consecutive lesson limit of 1"):
        await _create_lesson(
            db_session, teacher, student, datetime(2026, 10, 26, 10, tzinfo=UTC)
        )

    allowed = await _create_lesson(
        db_session, teacher, student, datetime(2026, 10, 26, 10, 1, tzinfo=UTC)
    )
    assert allowed.start == datetime(2026, 10, 26, 10, 1)


@pytest.mark.asyncio
async def test_daily_limit_groups_by_teacher_local_date_near_utc_midnight(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(
        db_session,
        timezone="Asia/Bangkok",
        work_start=time(6),
        working_days=[2],
        max_lessons_per_day=1,
    )
    await _create_lesson(
        db_session, teacher, student, datetime(2026, 10, 26, 23, 30, tzinfo=UTC)
    )

    with pytest.raises(ValidationError, match="daily lesson limit of 1"):
        await _create_lesson(
            db_session, teacher, student, datetime(2026, 10, 27, 0, 30, tzinfo=UTC)
        )


@pytest.mark.asyncio
async def test_recurrence_update_excludes_its_series_and_ignores_canceled_days(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(
        db_session, timezone="UTC", max_lessons_per_day=1
    )
    series_start = datetime(2026, 10, 26, 9, tzinfo=UTC)
    series_end = datetime(2026, 10, 26, 10, tzinfo=UTC)
    series = await create_recurrent_event(
        db_session,
        RecurrentLessonCreate(
            start=series_start,
            end=series_end,
            interval=7,
            interval_end=datetime(2026, 11, 9, 9, tzinfo=UTC),
            user_id=student.id,
        ),
        default_event_validator,
        teacher_id=teacher.id,
    )
    db_session.add(
        Event(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type=EventType.LESSON.value,
            start=datetime(2026, 11, 2, 11),
            end=datetime(2026, 11, 2, 12),
        )
    )
    await db_session.commit()

    updated = await update_recurrent_event(
        db_session,
        series,
        RecurrentLessonUpdate(
            start=series_start,
            end=series_end,
            interval=7,
            interval_end=datetime(2026, 11, 9, 9, tzinfo=UTC),
            user_id=student.id,
            cancels=[
                RecurrentCancel(
                    start=datetime(2026, 11, 2, 9, tzinfo=UTC),
                    end=datetime(2026, 11, 2, 10, tzinfo=UTC),
                )
            ],
        ),
        EventValidator(expected_event_type=EventType.LESSON),
        teacher_id=teacher.id,
        actor_is_teacher=True,
    )

    cancellation = await db_session.scalar(
        select(RecurrentCancels).where(
            RecurrentCancels.recurrent_event_id == updated.id
        )
    )
    assert cancellation is not None
    assert cancellation.start == datetime(2026, 11, 2, 9)


@pytest.mark.asyncio
async def test_recurring_lesson_checks_every_occurrence(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(
        db_session,
        timezone="UTC",
        work_start=time(9),
        work_end=time(18),
        working_days=[1, 2, 3, 4, 5],
    )

    with pytest.raises(ValidationError, match="non-working day"):
        await create_recurrent_event(
            db_session,
            RecurrentLessonCreate(
                start=datetime(2026, 10, 23, 9, tzinfo=UTC),
                end=datetime(2026, 10, 23, 10, tzinfo=UTC),
                interval=1,
                interval_end=datetime(2026, 10, 25, 9, tzinfo=UTC),
                user_id=student.id,
            ),
            default_event_validator,
            teacher_id=teacher.id,
        )


@pytest.mark.asyncio
async def test_teacher_local_recurrence_checks_dst_shifted_occurrence(
    db_session: AsyncSession,
) -> None:
    teacher, student = await _teacher_with_student(
        db_session,
        timezone="America/New_York",
        work_start=time(2),
        work_end=time(4, 15),
        working_days=[7],
    )

    with pytest.raises(ValidationError, match="after the teacher's work hours"):
        await create_recurrent_event(
            db_session,
            RecurrentLessonCreate(
                start=datetime(2026, 2, 22, 7, 30, tzinfo=UTC),
                end=datetime(2026, 2, 22, 8, 30, tzinfo=UTC),
                interval=7,
                interval_end=datetime(2026, 3, 10, 7, 30, tzinfo=UTC),
                user_id=student.id,
            ),
            default_event_validator,
            teacher_id=teacher.id,
        )


async def _register_and_login(
    client: AsyncClient, email: str, role: str
) -> tuple[str, str]:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "timezone": "UTC",
        },
    )
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert logged_in.status_code == 200, logged_in.text
    return registered.json()["id"], f"Bearer {logged_in.json()['access_token']}"


@pytest.mark.asyncio
async def test_api_uses_default_duration_and_actor_specific_duration_rules(
    client: AsyncClient,
) -> None:
    teacher_id, teacher_auth = await _register_and_login(
        client, "booking-teacher@test.com", "TEACHER"
    )
    profile = await client.post(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        json={"code": "t_BOOK01", "lesson_length": 45},
        headers={"Authorization": teacher_auth},
    )
    assert profile.status_code == 201, profile.text

    student_id, student_auth = await _register_and_login(
        client, "booking-student@test.com", "STUDENT"
    )
    linked = await client.post(
        "/api/v1/auth/me/teacher",
        json={"teacher_code": "t_BOOK01"},
        headers={"Authorization": student_auth},
    )
    assert linked.status_code == 200, linked.text

    lesson = await client.post(
        "/api/v1/lessons/",
        json={"start": "2026-10-26T09:00:00Z", "user_id": student_id},
        headers={"Authorization": student_auth},
    )
    assert lesson.status_code == 201, lesson.text
    assert lesson.json()["end"] == "2026-10-26T09:45:00+00:00"

    rejected = await client.post(
        "/api/v1/lessons/",
        json={
            "start": "2026-10-27T09:00:00Z",
            "end": "2026-10-27T10:00:00Z",
            "user_id": student_id,
        },
        headers={"Authorization": student_auth},
    )
    assert rejected.status_code == 422
    assert "configured lesson duration" in rejected.json()["detail"]

    teacher_override = await client.post(
        "/api/v1/lessons/",
        json={
            "start": "2026-10-28T09:00:00Z",
            "end": "2026-10-28T10:00:00Z",
            "user_id": student_id,
        },
        headers={"Authorization": teacher_auth},
    )
    assert teacher_override.status_code == 201, teacher_override.text
