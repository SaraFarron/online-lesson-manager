from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import TeacherProfile, User
from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.range_service import load_raw_occurrences
from backend.events.schemas import EventUpdate, RecurrentCancel, RecurrentEventUpdate
from backend.events.service import (
    delete_event,
    update_event,
    update_recurrent_event,
)
from backend.events.validators import EventValidator
from backend.exceptions import ValidationError


def _utc(day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


async def _users(
    session: AsyncSession,
) -> tuple[User, User, User]:
    teacher = User(
        id=uuid4(),
        email="edit-teacher@test.com",
        role="TEACHER",
        timezone="UTC",
    )
    first_student = User(
        id=uuid4(),
        email="edit-first-student@test.com",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
    )
    second_student = User(
        id=uuid4(),
        email="edit-second-student@test.com",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
    )
    session.add_all([teacher, first_student, second_student])
    session.add(
        TeacherProfile(
            teacher_id=teacher.id,
            code="t_EDIT001",
            working_days=[1, 2, 3, 4, 5, 6, 7],
        )
    )
    await session.flush()
    return teacher, first_student, second_student


async def _series(
    session: AsyncSession, teacher: User, subject: User
) -> RecurrentEvent:
    series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=subject.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 10, 1, 9),
        end=datetime(2026, 10, 1, 10),
        interval=7,
        interval_end=datetime(2026, 10, 29, 9),
        recurrence_mode=RecurrenceMode.TEACHER_LOCAL.value,
        recurrence_timezone="UTC",
    )
    session.add(series)
    await session.flush()
    return series


async def _cancel(
    session: AsyncSession,
    series: RecurrentEvent,
    day: int,
    *,
    end_hour: int = 10,
) -> RecurrentCancels:
    cancellation = RecurrentCancels(
        recurrent_event_id=series.id,
        start=datetime(2026, 10, day, 9),
        end=datetime(2026, 10, day, end_hour),
    )
    session.add(cancellation)
    await session.flush()
    return cancellation


async def _cancellations(
    session: AsyncSession, series_id: UUID
) -> list[RecurrentCancels]:
    return list(
        (
            await session.scalars(
                select(RecurrentCancels)
                .where(RecurrentCancels.recurrent_event_id == series_id)
                .order_by(RecurrentCancels.start)
            )
        ).all()
    )


def test_recurrent_update_schema_documents_cancellation_replacement() -> None:
    cancellation_description = RecurrentEventUpdate.model_json_schema()["properties"][
        "cancels"
    ]["description"]

    assert "preserve cancellations" in cancellation_description
    assert "empty list clears" in cancellation_description
    assert "full generated occurrence" in cancellation_description


def _update(
    subject_id: UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    interval: int = 7,
    interval_end: datetime | None = None,
    cancels: list[RecurrentCancel] | None = None,
) -> RecurrentEventUpdate:
    fields = {
        "user_id": subject_id,
        "start": start or _utc(1),
        "end": end or _utc(1, 10),
        "interval": interval,
        "interval_end": interval_end or _utc(29),
    }
    if cancels is not None:
        fields["cancels"] = cancels
    return RecurrentEventUpdate.model_validate(fields)


async def test_omitted_cancellations_preserve_them_for_subject_only_edit(
    db_session: AsyncSession,
) -> None:
    teacher, first_student, second_student = await _users(db_session)
    series = await _series(db_session, teacher, first_student)
    old_cancel = await _cancel(db_session, series, 8)
    await db_session.commit()

    updated = await update_recurrent_event(
        db_session,
        series,
        _update(second_student.id),
        EventValidator(),
        teacher_id=teacher.id,
    )

    assert updated.user_id == second_student.id
    assert await _cancellations(db_session, series.id) == [old_cancel]


async def test_recurrence_edits_persist_and_clear_omitted_cancellations(
    db_session: AsyncSession,
) -> None:
    teacher, first_student, second_student = await _users(db_session)
    series = await _series(db_session, teacher, first_student)
    await _cancel(db_session, series, 8)
    await db_session.commit()

    updated = await update_recurrent_event(
        db_session,
        series,
        _update(
            second_student.id,
            start=_utc(2),
            end=_utc(2, 10),
            interval=3,
            interval_end=_utc(20),
        ),
        EventValidator(),
        teacher_id=teacher.id,
    )

    assert updated.start == datetime(2026, 10, 2, 9)
    assert updated.end == datetime(2026, 10, 2, 10)
    assert updated.interval == 3
    assert updated.interval_end == datetime(2026, 10, 20, 9)
    assert updated.user_id == second_student.id
    assert updated.recurrence_timezone == "UTC"
    assert await _cancellations(db_session, series.id) == []

    occurrences = await load_raw_occurrences(
        db_session, teacher.id, _utc(5, 8), _utc(5, 11)
    )
    assert len(occurrences) == 1
    assert occurrences[0].start == _utc(5)
    assert occurrences[0].source.user_id == second_student.id


async def test_explicit_empty_cancellation_list_clears_existing_rows(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _users(db_session)
    series = await _series(db_session, teacher, student)
    await _cancel(db_session, series, 8)
    await db_session.commit()

    await update_recurrent_event(
        db_session,
        series,
        _update(student.id, cancels=[]),
        EventValidator(),
        teacher_id=teacher.id,
    )

    assert await _cancellations(db_session, series.id) == []


async def test_supplied_cancellations_replace_after_full_occurrence_validation(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _users(db_session)
    series = await _series(db_session, teacher, student)
    await _cancel(db_session, series, 8)
    await db_session.commit()

    await update_recurrent_event(
        db_session,
        series,
        _update(
            student.id,
            start=_utc(2),
            end=_utc(2, 10),
            interval=3,
            interval_end=_utc(20),
            cancels=[
                RecurrentCancel(start=_utc(11), end=_utc(11, 10)),
            ],
        ),
        EventValidator(),
        teacher_id=teacher.id,
    )

    cancellations = await _cancellations(db_session, series.id)
    assert len(cancellations) == 1
    assert cancellations[0].start == datetime(2026, 10, 11, 9)
    assert cancellations[0].end == datetime(2026, 10, 11, 10)
    visible = await load_raw_occurrences(
        db_session, teacher.id, _utc(8, 8), _utc(8, 11)
    )
    cancelled = await load_raw_occurrences(
        db_session, teacher.id, _utc(11, 8), _utc(11, 11)
    )
    assert len(visible) == 1
    assert cancelled == []


@pytest.mark.parametrize(
    ("cancels", "message"),
    [
        (
            [RecurrentCancel(start=_utc(5), end=_utc(5, 9, 30))],
            "full generated occurrence",
        ),
        (
            [RecurrentCancel(start=_utc(4), end=_utc(4, 10))],
            "does not match a generated occurrence",
        ),
        (
            [
                RecurrentCancel(start=_utc(5), end=_utc(5, 10)),
                RecurrentCancel(start=_utc(5), end=_utc(5, 10)),
            ],
            "Duplicate cancellation",
        ),
        (
            [RecurrentCancel(start=_utc(8), end=_utc(8, 10))],
            "does not match a generated occurrence",
        ),
    ],
)
async def test_invalid_replacement_rolls_back_series_and_cancellations(
    db_session: AsyncSession,
    cancels: list[RecurrentCancel],
    message: str,
) -> None:
    teacher, student, _ = await _users(db_session)
    series = await _series(db_session, teacher, student)
    old_cancel = await _cancel(db_session, series, 8)
    await db_session.commit()

    with pytest.raises(ValidationError, match=message):
        await update_recurrent_event(
            db_session,
            series,
            _update(
                student.id,
                start=_utc(2),
                end=_utc(2, 10),
                interval=3,
                interval_end=_utc(8),
                cancels=cancels,
            ),
            EventValidator(),
            teacher_id=teacher.id,
        )

    await db_session.refresh(series)
    assert series.start == datetime(2026, 10, 1, 9)
    assert series.end == datetime(2026, 10, 1, 10)
    assert series.interval == 7
    assert series.interval_end == datetime(2026, 10, 29, 9)
    assert series.user_id == student.id
    cancellations = await _cancellations(db_session, series.id)
    assert len(cancellations) == 1
    assert cancellations[0].id == old_cancel.id


async def test_recurrent_deletion_removes_cancellations_in_same_transaction(
    db_session: AsyncSession,
) -> None:
    teacher, student, _ = await _users(db_session)
    series = await _series(db_session, teacher, student)
    await _cancel(db_session, series, 8)
    await db_session.commit()

    await delete_event(db_session, series)

    assert await db_session.get(RecurrentEvent, series.id) is None
    assert await _cancellations(db_session, series.id) == []


async def test_one_off_update_persists_validated_subject_reassignment(
    db_session: AsyncSession,
) -> None:
    teacher, first_student, second_student = await _users(db_session)
    event = Event(
        teacher_id=teacher.id,
        user_id=first_student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 10, 1, 9),
        end=datetime(2026, 10, 1, 10),
    )
    db_session.add(event)
    await db_session.commit()

    updated = await update_event(
        db_session,
        event,
        EventUpdate(
            user_id=second_student.id,
            start=_utc(1),
            end=_utc(1, 10),
        ),
        EventValidator(),
        teacher_id=teacher.id,
    )

    assert updated.user_id == second_student.id
