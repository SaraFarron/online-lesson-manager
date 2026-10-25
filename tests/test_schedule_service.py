from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentEvent
from backend.events.occurrences import OccurrenceSourceKind
from backend.schedule.schemas import (
    SchedulePeriod,
    ScheduleResponse,
    TeacherLessonScheduleItem,
)
from backend.schedule.service import get_schedule

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
DAY = date(2026, 10, 19)


def _user(
    role: Roles,
    name: str | None,
    *,
    teacher_id: UUID | None = None,
    timezone: str = "UTC",
    is_active: bool = True,
) -> User:
    return User(
        id=uuid4(),
        email=f"test-{uuid4()}@example.com",
        full_name=name,
        role=role.value,
        timezone=timezone,
        teacher_id=teacher_id,
        is_active=is_active,
    )


async def _add_event(
    session: AsyncSession,
    teacher: User,
    subject: User,
    event_type: EventType,
    start_hour: int,
    end_hour: int,
) -> Event:
    occurrence = Event(
        teacher_id=teacher.id,
        user_id=subject.id,
        event_type=event_type.value,
        start=datetime(2026, 10, 19, start_hour),
        end=datetime(2026, 10, 19, end_hour),
    )
    session.add(occurrence)
    await session.flush()
    return occurrence


async def test_teacher_and_student_receive_only_their_authorized_safe_items(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, "Teacher")
    alice = _user(Roles.STUDENT, "Alice Example", teacher_id=teacher.id)
    bob = _user(Roles.STUDENT, "Bob Example", teacher_id=teacher.id)
    other_teacher = _user(Roles.TEACHER, "Unrelated Teacher")
    other_student = _user(
        Roles.STUDENT, "Unrelated Student", teacher_id=other_teacher.id
    )
    db_session.add_all([teacher, alice, bob, other_teacher, other_student])
    await db_session.flush()

    await _add_event(db_session, teacher, teacher, EventType.LESSON, 8, 9)
    await _add_event(db_session, teacher, alice, EventType.LESSON, 9, 10)
    await _add_event(db_session, teacher, bob, EventType.LESSON, 10, 11)
    await _add_event(db_session, teacher, teacher, EventType.BREAK, 11, 12)
    await _add_event(db_session, other_teacher, other_student, EventType.LESSON, 9, 10)
    await db_session.commit()

    teacher_result = await get_schedule(
        db_session, teacher, SchedulePeriod.DAY, DAY, NOW
    )
    student_result = await get_schedule(db_session, alice, SchedulePeriod.DAY, DAY, NOW)

    assert isinstance(teacher_result, ScheduleResponse)
    teacher_items = teacher_result.days[0].items
    assert [item.event_type for item in teacher_items] == [
        EventType.LESSON,
        EventType.LESSON,
        EventType.LESSON,
        EventType.BREAK,
    ]
    assert [
        item.subject_label
        for item in teacher_items
        if isinstance(item, TeacherLessonScheduleItem)
    ] == ["Personal lesson", "Alice Example", "Bob Example"]
    student_items = student_result.days[0].items
    assert [item.event_type for item in student_items] == [EventType.LESSON]
    assert student_items[0].source_id == teacher_items[1].source_id
    assert not isinstance(student_items[0], TeacherLessonScheduleItem)
    student_json = student_result.model_dump(mode="json")
    serialized_student = str(student_json)
    for sensitive_value in (
        "Bob Example",
        "Unrelated Teacher",
        "Unrelated Student",
        "email",
        "telegram_user_id",
        "teacher_profile",
    ):
        assert sensitive_value not in serialized_student
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


async def test_vacation_visibility_is_applied_before_student_scope(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, "Teacher")
    alice = _user(Roles.STUDENT, "Alice", teacher_id=teacher.id)
    bob = _user(Roles.STUDENT, "Bob", teacher_id=teacher.id)
    db_session.add_all([teacher, alice, bob])
    await db_session.flush()
    alice_lesson = await _add_event(db_session, teacher, alice, EventType.LESSON, 9, 10)
    await _add_event(db_session, teacher, bob, EventType.LESSON, 10, 11)
    await _add_event(db_session, teacher, alice, EventType.VACATION, 9, 10)
    await db_session.commit()

    student_result = await get_schedule(db_session, alice, SchedulePeriod.DAY, DAY, NOW)
    assert student_result.days[0].items == []

    await _add_event(db_session, teacher, teacher, EventType.VACATION, 8, 12)
    await db_session.commit()
    teacher_result = await get_schedule(
        db_session, teacher, SchedulePeriod.DAY, DAY, NOW
    )
    assert teacher_result.days[0].items == []
    student_result = await get_schedule(db_session, alice, SchedulePeriod.DAY, DAY, NOW)
    assert student_result.days[0].items == []
    assert await db_session.get(Event, alice_lesson.id) is not None


async def test_schedule_exposes_authorized_parent_linked_automatic_breaks(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, "Teacher")
    alice = _user(Roles.STUDENT, "Alice", teacher_id=teacher.id)
    bob = _user(Roles.STUDENT, "Bob", teacher_id=teacher.id)
    db_session.add_all([teacher, alice, bob])
    await db_session.flush()

    alice_lesson = Event(
        teacher_id=teacher.id,
        user_id=alice.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 10, 19, 9),
        end=datetime(2026, 10, 19, 10),
        post_break_minutes=5,
    )
    bob_lesson = Event(
        teacher_id=teacher.id,
        user_id=bob.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 10, 19, 11),
        end=datetime(2026, 10, 19, 12),
        post_break_minutes=5,
    )
    vacation_overlapping_only_auto_break = Event(
        teacher_id=teacher.id,
        user_id=alice.id,
        event_type=EventType.VACATION.value,
        start=datetime(2026, 10, 19, 10, 2),
        end=datetime(2026, 10, 19, 10, 4),
    )
    vacation_hiding_parent = Event(
        teacher_id=teacher.id,
        user_id=alice.id,
        event_type=EventType.VACATION.value,
        start=datetime(2026, 10, 19, 12, 30),
        end=datetime(2026, 10, 19, 12, 45),
    )
    hidden_lesson = Event(
        teacher_id=teacher.id,
        user_id=alice.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 10, 19, 12),
        end=datetime(2026, 10, 19, 13),
        post_break_minutes=5,
    )
    series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=alice.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 10, 19, 14),
        end=datetime(2026, 10, 19, 15),
        interval=7,
        interval_end=datetime(2026, 10, 27, 14),
        recurrence_mode=RecurrenceMode.UTC_STEP.value,
        recurrence_timezone=None,
        post_break_minutes=5,
    )
    manual_break = Event(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type=EventType.BREAK.value,
        start=datetime(2026, 10, 19, 16),
        end=datetime(2026, 10, 19, 17),
    )
    db_session.add_all(
        [
            alice_lesson,
            bob_lesson,
            vacation_overlapping_only_auto_break,
            vacation_hiding_parent,
            hidden_lesson,
            series,
            manual_break,
        ]
    )
    await db_session.commit()

    teacher_result = await get_schedule(
        db_session, teacher, SchedulePeriod.DAY, DAY, NOW
    )
    alice_result = await get_schedule(db_session, alice, SchedulePeriod.DAY, DAY, NOW)

    teacher_items = teacher_result.days[0].items
    auto_breaks = [
        item
        for item in teacher_items
        if item.source_kind == OccurrenceSourceKind.AUTOMATIC_BREAK
    ]
    assert {
        (item.source_id, item.parent_kind, item.parent_start) for item in auto_breaks
    } == {
        (
            alice_lesson.id,
            OccurrenceSourceKind.EVENT,
            datetime(2026, 10, 19, 9, tzinfo=UTC),
        ),
        (
            bob_lesson.id,
            OccurrenceSourceKind.EVENT,
            datetime(2026, 10, 19, 11, tzinfo=UTC),
        ),
        (
            series.id,
            OccurrenceSourceKind.RECURRENT_EVENT,
            datetime(2026, 10, 19, 14, tzinfo=UTC),
        ),
    }
    assert all(item.event_type == EventType.BREAK for item in auto_breaks)
    assert any(item.source_id == manual_break.id for item in teacher_items)
    assert not any(item.source_id == hidden_lesson.id for item in teacher_items)

    alice_items = alice_result.days[0].items
    assert {item.source_id for item in alice_items} >= {alice_lesson.id, series.id}
    assert all(item.source_id != bob_lesson.id for item in alice_items)
    assert {
        item.source_id
        for item in alice_items
        if item.source_kind == OccurrenceSourceKind.AUTOMATIC_BREAK
    } == {alice_lesson.id, series.id}


async def test_missing_or_blank_student_name_uses_neutral_fallback(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, "Teacher")
    unnamed = _user(Roles.STUDENT, "   ", teacher_id=teacher.id)
    nameless = _user(Roles.STUDENT, None, teacher_id=teacher.id)
    db_session.add_all([teacher, unnamed, nameless])
    await db_session.flush()
    await _add_event(db_session, teacher, unnamed, EventType.LESSON, 9, 10)
    await _add_event(db_session, teacher, nameless, EventType.LESSON, 10, 11)
    await db_session.commit()

    result = await get_schedule(db_session, teacher, SchedulePeriod.DAY, DAY, NOW)

    assert [
        item.subject_label
        for item in result.days[0].items
        if isinstance(item, TeacherLessonScheduleItem)
    ] == ["Unnamed student", "Unnamed student"]


@pytest.mark.parametrize(
    ("viewer_factory", "expected_status"),
    [
        (lambda: _user(Roles.ADMIN, "Admin"), 403),
        (lambda: _user(Roles.STUDENT, "Unlinked Student"), 400),
        (
            lambda: _user(Roles.STUDENT, "Missing Teacher Student", teacher_id=uuid4()),
            409,
        ),
    ],
)
async def test_unsupported_viewer_linkage_fails_explicitly(
    db_session: AsyncSession,
    viewer_factory: Callable[[], User],
    expected_status: int,
) -> None:
    viewer = viewer_factory()
    with pytest.raises(HTTPException) as exc_info:
        await get_schedule(db_session, viewer, SchedulePeriod.DAY, DAY, NOW)
    assert exc_info.value.status_code == expected_status


@pytest.mark.parametrize(
    ("teacher_role", "is_active"),
    [(Roles.STUDENT, True), (Roles.TEACHER, False)],
)
async def test_invalid_linked_teacher_fails_with_conflict(
    db_session: AsyncSession,
    teacher_role: Roles,
    is_active: bool,
) -> None:
    linked_teacher = _user(teacher_role, "Linked Teacher", is_active=is_active)
    student = _user(Roles.STUDENT, "Student", teacher_id=linked_teacher.id)
    db_session.add_all([linked_teacher, student])
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await get_schedule(db_session, student, SchedulePeriod.DAY, DAY, NOW)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Linked teacher cannot provide a schedule"


async def test_default_date_uses_viewer_timezone_near_utc_midnight(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, "Bangkok Teacher", timezone="Asia/Bangkok")
    db_session.add(teacher)
    await db_session.commit()

    result = await get_schedule(
        db_session,
        teacher,
        SchedulePeriod.DAY,
        None,
        datetime(2026, 10, 18, 18, 30, tzinfo=UTC),
    )

    assert result.days[0].date == date(2026, 10, 19)
    assert result.timezone == "Asia/Bangkok"
    assert result.generated_at == datetime(2026, 10, 18, 18, 30, tzinfo=UTC)


async def test_database_query_count_is_bounded_and_read_only(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, "Teacher")
    students = [
        _user(Roles.STUDENT, f"Student {index}", teacher_id=teacher.id)
        for index in range(5)
    ]
    db_session.add_all([teacher, *students])
    await db_session.flush()
    for index in range(10):
        await _add_event(
            db_session,
            teacher,
            students[index % len(students)],
            EventType.LESSON,
            index + 1,
            index + 2,
        )
    await db_session.commit()

    statements: list[str] = []

    def record_statement(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        result = await get_schedule(db_session, teacher, SchedulePeriod.WEEK, DAY, NOW)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    select_count = sum(
        statement.lstrip().upper().startswith("SELECT") for statement in statements
    )
    assert select_count == 5
    assert sum(len(day.items) for day in result.days) == 10
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
