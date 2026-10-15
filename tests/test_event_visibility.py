from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.range_service import load_raw_occurrences
from backend.events.service import delete_event
from backend.events.visibility import (
    VacationVisibilityReason,
    filter_vacation_visibility,
)


def _user(role: str, email: str, *, teacher_id: UUID | None = None) -> User:
    return User(
        id=uuid4(),
        email=email,
        role=role,
        timezone="UTC",
        teacher_id=teacher_id,
    )


async def _event(
    session: AsyncSession,
    teacher: User,
    subject: User,
    event_type: EventType,
    start: datetime,
    end: datetime,
) -> Event:
    event = Event(
        teacher_id=teacher.id,
        user_id=subject.id,
        event_type=event_type.value,
        start=start,
        end=end,
    )
    session.add(event)
    await session.flush()
    return event


async def test_teacher_vacation_hides_all_overlapping_event_types(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "teacher-vacation@test.com")
    student_a = _user("STUDENT", "teacher-vacation-a@test.com", teacher_id=teacher.id)
    student_b = _user("STUDENT", "teacher-vacation-b@test.com", teacher_id=teacher.id)
    db_session.add_all([teacher, student_a, student_b])
    await db_session.flush()

    teacher_break = await _event(
        db_session,
        teacher,
        teacher,
        EventType.BREAK,
        datetime(2026, 10, 2, 9),
        datetime(2026, 10, 2, 10),
    )
    lesson_a = await _event(
        db_session,
        teacher,
        student_a,
        EventType.LESSON,
        datetime(2026, 10, 2, 9),
        datetime(2026, 10, 2, 10),
    )
    lesson_b = await _event(
        db_session,
        teacher,
        student_b,
        EventType.LESSON,
        datetime(2026, 10, 2, 9),
        datetime(2026, 10, 2, 10),
    )
    vacation = await _event(
        db_session,
        teacher,
        teacher,
        EventType.VACATION,
        datetime(2026, 10, 2, 9, 30),
        datetime(2026, 10, 2, 10, 30),
    )
    await db_session.commit()

    raw = await load_raw_occurrences(
        db_session,
        teacher.id,
        datetime(2026, 10, 2, 8),
        datetime(2026, 10, 2, 11),
    )
    visible = filter_vacation_visibility(raw, teacher.id)

    assert visible.visible == ()
    assert {item.occurrence.source.id for item in visible.hidden} == {
        teacher_break.id,
        lesson_a.id,
        lesson_b.id,
        vacation.id,
    }
    assert all(
        item.reasons == (VacationVisibilityReason.TEACHER_VACATION,)
        for item in visible.hidden
    )
    assert len(raw) == 4
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


async def test_student_vacation_hides_only_that_subject_and_uses_half_open_overlap(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "student-vacation-teacher@test.com")
    student_a = _user("STUDENT", "student-vacation-a@test.com", teacher_id=teacher.id)
    student_b = _user("STUDENT", "student-vacation-b@test.com", teacher_id=teacher.id)
    db_session.add_all([teacher, student_a, student_b])
    await db_session.flush()

    vacation = await _event(
        db_session,
        teacher,
        student_a,
        EventType.VACATION,
        datetime(2026, 10, 2, 9),
        datetime(2026, 10, 2, 10),
    )
    overlapping_lesson = await _event(
        db_session,
        teacher,
        student_a,
        EventType.LESSON,
        datetime(2026, 10, 2, 9, 30),
        datetime(2026, 10, 2, 10, 30),
    )
    adjacent_lesson = await _event(
        db_session,
        teacher,
        student_a,
        EventType.LESSON,
        datetime(2026, 10, 2, 8),
        datetime(2026, 10, 2, 9),
    )
    other_student_lesson = await _event(
        db_session,
        teacher,
        student_b,
        EventType.LESSON,
        datetime(2026, 10, 2, 9, 30),
        datetime(2026, 10, 2, 10, 30),
    )
    teacher_break = await _event(
        db_session,
        teacher,
        teacher,
        EventType.BREAK,
        datetime(2026, 10, 2, 9),
        datetime(2026, 10, 2, 10),
    )
    await db_session.commit()

    raw = await load_raw_occurrences(
        db_session,
        teacher.id,
        datetime(2026, 10, 2, 8),
        datetime(2026, 10, 2, 11),
    )
    result = filter_vacation_visibility(raw, teacher.id)

    assert {item.occurrence.source.id for item in result.hidden} == {
        vacation.id,
        overlapping_lesson.id,
    }
    assert all(
        item.reasons == (VacationVisibilityReason.STUDENT_VACATION,)
        for item in result.hidden
    )
    assert {item.source.id for item in result.visible} == {
        adjacent_lesson.id,
        other_student_lesson.id,
        teacher_break.id,
    }
    assert len(raw) == 5


async def test_removing_vacation_reveals_same_event_identity_without_mutation(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "vacation-removal-teacher@test.com")
    student = _user(
        "STUDENT", "vacation-removal-student@test.com", teacher_id=teacher.id
    )
    db_session.add_all([teacher, student])
    await db_session.flush()
    lesson = await _event(
        db_session,
        teacher,
        student,
        EventType.LESSON,
        datetime(2026, 10, 2, 9),
        datetime(2026, 10, 2, 10),
    )
    vacation = await _event(
        db_session,
        teacher,
        student,
        EventType.VACATION,
        datetime(2026, 10, 2, 9, 30),
        datetime(2026, 10, 2, 10, 30),
    )
    await db_session.commit()

    before = await load_raw_occurrences(
        db_session,
        teacher.id,
        datetime(2026, 10, 2, 8),
        datetime(2026, 10, 2, 11),
    )
    before_result = filter_vacation_visibility(before, teacher.id)
    assert any(item.occurrence.source.id == lesson.id for item in before_result.hidden)
    assert len(before) == 2

    await delete_event(db_session, vacation)

    after = await load_raw_occurrences(
        db_session,
        teacher.id,
        datetime(2026, 10, 2, 8),
        datetime(2026, 10, 2, 11),
    )
    after_result = filter_vacation_visibility(after, teacher.id)
    visible_lesson = next(
        occurrence
        for occurrence in after_result.visible
        if occurrence.source.id == lesson.id
    )
    assert visible_lesson.identity == next(
        item.occurrence.identity
        for item in before_result.hidden
        if item.occurrence.source.id == lesson.id
    )
    assert await db_session.get(Event, lesson.id) is not None
    assert await db_session.get(Event, vacation.id) is None


async def test_break_overlap_is_not_arbitrated_and_recurrence_cancellation_is_separate(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "break-visibility-teacher@test.com")
    student = _user(
        "STUDENT", "break-visibility-student@test.com", teacher_id=teacher.id
    )
    db_session.add_all([teacher, student])
    await db_session.flush()
    teacher_break = await _event(
        db_session,
        teacher,
        teacher,
        EventType.BREAK,
        datetime(2026, 10, 8, 9),
        datetime(2026, 10, 8, 10),
    )
    lesson = await _event(
        db_session,
        teacher,
        student,
        EventType.LESSON,
        datetime(2026, 10, 8, 9),
        datetime(2026, 10, 8, 10),
    )
    series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 10, 1, 9),
        end=datetime(2026, 10, 1, 10),
        interval=7,
        interval_end=datetime(2026, 10, 29, 9),
        recurrence_mode="TEACHER_LOCAL",
        recurrence_timezone="UTC",
    )
    db_session.add(series)
    await db_session.flush()
    cancellation = RecurrentCancels(
        recurrent_event_id=series.id,
        start=datetime(2026, 10, 8, 9),
        end=datetime(2026, 10, 8, 10),
    )
    db_session.add(cancellation)
    await db_session.commit()

    raw = await load_raw_occurrences(
        db_session,
        teacher.id,
        datetime(2026, 10, 8, 8),
        datetime(2026, 10, 8, 11),
    )
    result = filter_vacation_visibility(raw, teacher.id)

    assert {item.source.id for item in result.visible} == {
        teacher_break.id,
        lesson.id,
    }
    assert result.hidden == ()
    assert all(item.source.id != series.id for item in raw)
    assert await db_session.get(RecurrentEvent, series.id) is not None
    assert await db_session.get(RecurrentCancels, cancellation.id) is not None
