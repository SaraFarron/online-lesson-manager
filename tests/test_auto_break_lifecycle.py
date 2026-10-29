from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import TeacherProfile, User
from backend.breaks.schemas import BreakCreate
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.range_service import load_raw_occurrences
from backend.events.service import (
    cancel_recurrent_lesson_occurrence,
    create_event,
    create_recurrent_event,
    delete_event,
    move_recurrent_lesson_occurrence,
    update_event,
    update_recurrent_event,
)
from backend.events.validators import EventValidator
from backend.exceptions import Conflict, ValidationError
from backend.lessons.schemas import (
    LessonCreate,
    LessonUpdate,
    RecurrentLessonCreate,
    RecurrentLessonUpdate,
    RecurrentOccurrenceCancelRequest,
    RecurrentOccurrenceMoveRequest,
)
from backend.lessons.validators import default_lesson_validator


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


async def _accounts(
    session: AsyncSession, *, break_minutes: int = 5
) -> tuple[User, User, TeacherProfile]:
    teacher = User(id=uuid4(), role="TEACHER", timezone="UTC")
    student = User(id=uuid4(), role="STUDENT", timezone="UTC", teacher_id=teacher.id)
    profile = TeacherProfile(
        teacher_id=teacher.id,
        code=f"t_{uuid4().hex[:8]}",
        work_start=time(9),
        work_end=time(18),
        working_days=[1, 2, 3, 4, 5, 6, 7],
        lesson_length=60,
        between_lessons_break=break_minutes,
        max_consecutive_lessons=4,
        max_lessons_per_day=6,
    )
    session.add_all([teacher, student, profile])
    await session.commit()
    return teacher, student, profile


async def _create_lesson(
    session: AsyncSession, teacher: User, student: User, day: int, hour: int = 10
) -> Event:
    return await create_event(
        session,
        LessonCreate(
            start=_at(day, hour),
            end=_at(day, hour + 1),
            user_id=student.id,
        ),
        default_lesson_validator,
        teacher.id,
    )


async def _create_series(
    session: AsyncSession, teacher: User, student: User, day: int = 2
) -> RecurrentEvent:
    return await create_recurrent_event(
        session,
        RecurrentLessonCreate(
            start=_at(day, 9),
            end=_at(day, 10),
            user_id=student.id,
            interval=7,
            interval_end=_at(30, 9),
        ),
        default_lesson_validator,
        teacher.id,
    )


async def test_creation_snapshots_preferences_and_updates_preserve_them(
    db_session: AsyncSession,
) -> None:
    teacher, student, profile = await _accounts(db_session)
    one_off = await _create_lesson(db_session, teacher, student, 2)
    series = await _create_series(db_session, teacher, student, 3)

    assert one_off.post_break_minutes == 5
    assert series.post_break_minutes == 5

    profile.between_lessons_break = 0
    await db_session.commit()
    updated_one_off = await update_event(
        db_session,
        one_off,
        LessonUpdate(user_id=student.id, start=_at(2, 12), end=_at(2, 13)),
        default_lesson_validator,
        teacher.id,
    )
    updated_series = await update_recurrent_event(
        db_session,
        series,
        RecurrentLessonUpdate(
            user_id=student.id,
            start=_at(3, 12),
            end=_at(3, 13),
            interval=7,
            interval_end=_at(30, 12),
        ),
        default_lesson_validator,
        teacher.id,
    )

    assert updated_one_off.post_break_minutes == 5
    assert updated_series.post_break_minutes == 5

    new_one_off = await _create_lesson(db_session, teacher, student, 4)
    new_series = await _create_series(db_session, teacher, student, 5)
    assert new_one_off.post_break_minutes == 0
    assert new_series.post_break_minutes == 0


async def test_move_and_cancel_change_only_the_affected_series_breaks(
    db_session: AsyncSession,
) -> None:
    teacher, student, profile = await _accounts(db_session, break_minutes=5)
    series = await _create_series(db_session, teacher, student)
    profile.between_lessons_break = 20
    await db_session.commit()

    _, replacement, _ = await move_recurrent_lesson_occurrence(
        db_session,
        series.id,
        RecurrentOccurrenceMoveRequest(
            occurrence_start=_at(9, 9),
            occurrence_end=_at(9, 10),
            replacement_start=_at(9, 11),
            replacement_end=_at(9, 12),
        ),
        teacher,
        teacher.id,
        default_lesson_validator,
    )

    assert replacement.post_break_minutes == 5
    moved_day = await load_raw_occurrences(
        db_session,
        teacher.id,
        _at(9, 10, 55),
        _at(9, 12, 10),
        include_automatic_breaks=True,
    )
    assert [(item.source.event_type, item.start, item.end) for item in moved_day] == [
        (EventType.LESSON, _at(9, 11), _at(9, 12)),
        (EventType.BREAK, _at(9, 12), _at(9, 12, 5)),
    ]
    assert (
        len(
            (
                await db_session.scalars(
                    select(RecurrentCancels).where(
                        RecurrentCancels.recurrent_event_id == series.id
                    )
                )
            ).all()
        )
        == 1
    )

    await cancel_recurrent_lesson_occurrence(
        db_session,
        series.id,
        RecurrentOccurrenceCancelRequest(
            occurrence_start=_at(16, 9), occurrence_end=_at(16, 10)
        ),
        teacher,
        teacher.id,
    )
    with pytest.raises(Conflict):
        await cancel_recurrent_lesson_occurrence(
            db_session,
            series.id,
            RecurrentOccurrenceCancelRequest(
                occurrence_start=_at(16, 9), occurrence_end=_at(16, 10)
            ),
            teacher,
            teacher.id,
        )
    canceled_day = await load_raw_occurrences(
        db_session,
        teacher.id,
        _at(16, 8),
        _at(16, 10, 10),
        include_automatic_breaks=True,
    )
    assert canceled_day == []

    unaffected_day = await load_raw_occurrences(
        db_session,
        teacher.id,
        _at(2, 8),
        _at(2, 10, 10),
        include_automatic_breaks=True,
    )
    assert len(unaffected_day) == 2
    assert unaffected_day[1].end == _at(2, 10, 5)


async def test_failed_occurrence_move_rolls_back_cancellation_and_replacement(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _accounts(db_session)
    series = await _create_series(db_session, teacher, student)
    teacher_id = teacher.id
    series_id = series.id
    conflict = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=_at(9, 11).replace(tzinfo=None),
        end=_at(9, 12).replace(tzinfo=None),
    )
    db_session.add(conflict)
    await db_session.commit()

    with pytest.raises(ValidationError, match="overlaps"):
        await move_recurrent_lesson_occurrence(
            db_session,
            series_id,
            RecurrentOccurrenceMoveRequest(
                occurrence_start=_at(9, 9),
                occurrence_end=_at(9, 10),
                replacement_start=_at(9, 11),
                replacement_end=_at(9, 12),
            ),
            teacher,
            teacher_id,
            default_lesson_validator,
        )

    await db_session.refresh(series)
    cancellations = list(
        (
            await db_session.scalars(
                select(RecurrentCancels).where(
                    RecurrentCancels.recurrent_event_id == series_id
                )
            )
        ).all()
    )
    assert cancellations == []
    assert (
        await db_session.scalar(
            select(Event).where(Event.start == _at(9, 11).replace(tzinfo=None))
        )
        == conflict
    )
    occurrence = await load_raw_occurrences(
        db_session,
        teacher_id,
        _at(9, 8),
        _at(9, 10, 10),
        include_automatic_breaks=True,
    )
    assert len(occurrence) == 2
    assert occurrence[1].end == _at(9, 10, 5)


async def test_manual_break_displacement_removes_the_lesson_reservation(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _accounts(db_session)
    lesson = await _create_lesson(db_session, teacher, student, 2, hour=9)

    await create_event(
        db_session,
        BreakCreate(start=_at(2, 9, 30), end=_at(2, 10, 30), user_id=teacher.id),
        EventValidator(expected_event_type=EventType.BREAK),
        teacher.id,
        confirm_displacement=True,
        actor_is_teacher=True,
    )

    assert await db_session.get(Event, lesson.id) is None
    occurrences = await load_raw_occurrences(
        db_session,
        teacher.id,
        _at(2, 9),
        _at(2, 10, 35),
        include_automatic_breaks=True,
    )
    assert len(occurrences) == 1
    assert occurrences[0].source.event_type == EventType.BREAK
    assert occurrences[0].source.id != lesson.id


async def test_deleting_displacing_break_does_not_restore_lesson(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _accounts(db_session)
    lesson = await _create_lesson(db_session, teacher, student, 2, hour=9)
    manual_break = await create_event(
        db_session,
        BreakCreate(start=_at(2, 9, 30), end=_at(2, 10, 30), user_id=teacher.id),
        EventValidator(expected_event_type=EventType.BREAK),
        teacher.id,
        confirm_displacement=True,
        actor_is_teacher=True,
    )

    await delete_event(db_session, manual_break)

    assert await db_session.scalar(select(Event).where(Event.id == lesson.id)) is None


async def test_manual_break_displacement_cancels_only_the_matching_series_break(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _accounts(db_session)
    series = await _create_series(db_session, teacher, student)

    await create_event(
        db_session,
        BreakCreate(start=_at(9, 9, 30), end=_at(9, 10, 30), user_id=teacher.id),
        EventValidator(expected_event_type=EventType.BREAK),
        teacher.id,
        confirm_displacement=True,
        actor_is_teacher=True,
    )

    canceled_day = await load_raw_occurrences(
        db_session,
        teacher.id,
        _at(9, 8),
        _at(9, 10, 35),
        include_automatic_breaks=True,
    )
    unaffected_day = await load_raw_occurrences(
        db_session,
        teacher.id,
        _at(2, 8),
        _at(2, 10, 10),
        include_automatic_breaks=True,
    )
    cancellations = list(
        (
            await db_session.scalars(
                select(RecurrentCancels).where(
                    RecurrentCancels.recurrent_event_id == series.id
                )
            )
        ).all()
    )

    assert len(canceled_day) == 1
    assert canceled_day[0].source.event_type == EventType.BREAK
    assert len(unaffected_day) == 2
    assert unaffected_day[1].end == _at(2, 10, 5)
    assert len(cancellations) == 1
    assert cancellations[0].start == _at(9, 9).replace(tzinfo=None)


async def test_deleting_series_removes_derived_breaks_but_not_manual_breaks(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _accounts(db_session)
    series = await _create_series(db_session, teacher, student)
    manual = Event(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type=EventType.BREAK.value,
        start=_at(2, 11).replace(tzinfo=None),
        end=_at(2, 11, 15).replace(tzinfo=None),
    )
    db_session.add(manual)
    await db_session.commit()

    await delete_event(db_session, series)

    occurrences = await load_raw_occurrences(
        db_session,
        teacher.id,
        _at(2, 8),
        _at(2, 11, 20),
        include_automatic_breaks=True,
    )
    assert [item.source.id for item in occurrences] == [manual.id]
    assert occurrences[0].source.event_type == EventType.BREAK


async def test_move_into_saved_automatic_break_is_rejected_without_mutation(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _accounts(db_session, break_minutes=5)
    blocking_lesson = await create_event(
        db_session,
        LessonCreate(
            start=_at(2, 9),
            end=_at(2, 10),
            user_id=student.id,
        ),
        default_lesson_validator,
        teacher.id,
    )
    moving_lesson = await create_event(
        db_session,
        LessonCreate(
            start=_at(2, 12),
            end=_at(2, 13),
            user_id=student.id,
        ),
        default_lesson_validator,
        teacher.id,
    )

    with pytest.raises(ValidationError):
        await update_event(
            db_session,
            moving_lesson,
            LessonUpdate(
                user_id=student.id,
                start=_at(2, 10, 4),
                end=_at(2, 11, 4),
            ),
            default_lesson_validator,
            teacher.id,
        )

    await db_session.refresh(moving_lesson)
    assert moving_lesson.start == _at(2, 12).replace(tzinfo=None)
    assert blocking_lesson.post_break_minutes == 5
