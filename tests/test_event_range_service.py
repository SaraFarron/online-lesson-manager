from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.occurrences import OccurrenceSourceKind
from backend.events.range_service import load_raw_occurrences
from backend.events.time_utils import from_storage_utc
from backend.exceptions import ValidationError


def _user(role: str, email: str, *, teacher_id: UUID | None = None) -> User:
    return User(
        id=uuid4(),
        email=email,
        role=role,
        timezone="UTC",
        teacher_id=teacher_id,
    )


async def test_range_service_scopes_and_bulk_loads_raw_active_occurrences(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "range-teacher@test.com")
    student = _user("STUDENT", "range-student@test.com", teacher_id=teacher.id)
    other_student = _user(
        "STUDENT", "range-other-student@test.com", teacher_id=teacher.id
    )
    other_teacher = _user("TEACHER", "range-other-teacher@test.com")
    db_session.add_all([teacher, student, other_student, other_teacher])
    await db_session.flush()

    lower_edge_event = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type="LESSON",
        start=datetime(2026, 10, 1, 23),
        end=datetime(2026, 10, 2, 1),
    )
    teacher_break = Event(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type="BREAK",
        start=datetime(2026, 10, 2, 12),
        end=datetime(2026, 10, 2, 13),
    )
    upper_edge_vacation = Event(
        teacher_id=teacher.id,
        user_id=other_student.id,
        event_type="VACATION",
        start=datetime(2026, 10, 3, 0),
        end=datetime(2026, 10, 3, 2),
    )
    foreign_event = Event(
        teacher_id=other_teacher.id,
        user_id=other_teacher.id,
        event_type="LESSON",
        start=datetime(2026, 10, 2, 14),
        end=datetime(2026, 10, 2, 15),
    )
    lower_edge_series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type="LESSON",
        start=datetime(2026, 10, 1, 23),
        end=datetime(2026, 10, 2, 1),
        interval=1,
        interval_end=datetime(2026, 10, 10, 23),
    )
    second_series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=other_student.id,
        event_type="LESSON",
        start=datetime(2026, 10, 2, 10),
        end=datetime(2026, 10, 2, 11),
        interval=7,
        interval_end=datetime(2026, 10, 16, 10),
    )
    foreign_series = RecurrentEvent(
        teacher_id=other_teacher.id,
        user_id=other_teacher.id,
        event_type="LESSON",
        start=datetime(2026, 10, 2, 9),
        end=datetime(2026, 10, 2, 10),
        interval=7,
        interval_end=datetime(2026, 10, 16, 9),
    )
    db_session.add_all(
        [
            lower_edge_event,
            teacher_break,
            upper_edge_vacation,
            foreign_event,
            lower_edge_series,
            second_series,
            foreign_series,
        ]
    )
    await db_session.flush()
    canceled_start = datetime(2026, 10, 2, 23)
    db_session.add(
        RecurrentCancels(
            recurrent_event_id=lower_edge_series.id,
            start=canceled_start,
            end=datetime(2026, 10, 3, 1),
        )
    )
    await db_session.commit()

    statements: list[str] = []

    def count_statement(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        occurrences = await load_raw_occurrences(
            db_session,
            teacher.id,
            datetime(2026, 10, 2, 0, 30),
            datetime(2026, 10, 3, 0, 30),
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert len(statements) == 3
    assert len(occurrences) == 5
    assert all(occurrence.source.teacher_id == teacher.id for occurrence in occurrences)
    assert {occurrence.source.event_type.value for occurrence in occurrences} == {
        "LESSON",
        "BREAK",
        "VACATION",
    }
    assert {occurrence.source.user_id for occurrence in occurrences} == {
        teacher.id,
        student.id,
        other_student.id,
    }
    assert all(occurrence.start.tzinfo is not None for occurrence in occurrences)
    assert all(
        occurrence.source.id != foreign_event.id
        and occurrence.source.id != foreign_series.id
        for occurrence in occurrences
    )
    assert not any(
        occurrence.source.id == lower_edge_series.id
        and occurrence.start == from_storage_utc(canceled_start)
        for occurrence in occurrences
    )
    assert any(
        occurrence.source.kind == OccurrenceSourceKind.RECURRENT_EVENT
        and occurrence.source.id == second_series.id
        for occurrence in occurrences
    )
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


async def test_range_service_rejects_malformed_relevant_cancellation(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "invalid-cancel-teacher@test.com")
    db_session.add(teacher)
    await db_session.flush()
    series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type="LESSON",
        start=datetime(2026, 10, 1, 9),
        end=datetime(2026, 10, 1, 10),
        interval=7,
        interval_end=datetime(2026, 10, 22, 9),
    )
    db_session.add(series)
    await db_session.flush()
    db_session.add(
        RecurrentCancels(
            recurrent_event_id=series.id,
            start=datetime(2026, 10, 8, 9),
            end=datetime(2026, 10, 8, 9, 30),
        )
    )
    await db_session.commit()

    with pytest.raises(ValidationError, match="full generated occurrence"):
        await load_raw_occurrences(
            db_session,
            teacher.id,
            datetime(2026, 10, 8, 8),
            datetime(2026, 10, 8, 11),
        )


async def test_range_service_ignores_malformed_cancellation_outside_range(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "unrelated-cancel-teacher@test.com")
    db_session.add(teacher)
    await db_session.flush()
    series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type="LESSON",
        start=datetime(2026, 10, 1, 9),
        end=datetime(2026, 10, 1, 10),
        interval=7,
        interval_end=datetime(2026, 10, 22, 9),
    )
    db_session.add(series)
    await db_session.flush()
    db_session.add(
        RecurrentCancels(
            recurrent_event_id=series.id,
            start=datetime(2026, 10, 15, 9),
            end=datetime(2026, 10, 15, 8),
        )
    )
    await db_session.commit()

    occurrences = await load_raw_occurrences(
        db_session,
        teacher.id,
        datetime(2026, 10, 8, 8),
        datetime(2026, 10, 8, 11),
    )

    assert len(occurrences) == 1
    assert occurrences[0].start == from_storage_utc(datetime(2026, 10, 8, 9))


async def test_range_service_keeps_last_occurrence_that_crosses_after_cutoff(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "cutoff-tail-teacher@test.com")
    db_session.add(teacher)
    await db_session.flush()
    series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type="LESSON",
        start=datetime(2026, 10, 1, 23),
        end=datetime(2026, 10, 3, 1),
        interval=7,
        interval_end=datetime(2026, 10, 9, 0),
    )
    db_session.add(series)
    await db_session.commit()

    occurrences = await load_raw_occurrences(
        db_session,
        teacher.id,
        datetime(2026, 10, 10, 0, 30),
        datetime(2026, 10, 10, 1),
    )

    assert len(occurrences) == 1
    assert occurrences[0].start == from_storage_utc(datetime(2026, 10, 8, 23))
    assert occurrences[0].end == from_storage_utc(datetime(2026, 10, 10, 1))


async def test_range_service_rejects_invalid_overlapping_event_data(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "invalid-event-teacher@test.com")
    db_session.add(teacher)
    await db_session.flush()
    invalid_event = Event(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type="LESSON",
        start=datetime(2026, 10, 8, 10),
        end=datetime(2026, 10, 8, 9),
    )
    db_session.add(invalid_event)
    await db_session.commit()

    with pytest.raises(ValidationError, match="invalid time range"):
        await load_raw_occurrences(
            db_session,
            teacher.id,
            datetime(2026, 10, 8, 8),
            datetime(2026, 10, 8, 11),
        )


@pytest.mark.parametrize(
    ("range_start", "range_end", "message"),
    [
        (
            datetime(2026, 1, 2),
            datetime(2026, 1, 1),
            "before range end",
        ),
        (
            datetime(2026, 1, 1),
            datetime(2026, 2, 2),
            "cannot exceed 31 days",
        ),
    ],
)
async def test_range_service_validates_range_bounds(
    db_session: AsyncSession,
    range_start: datetime,
    range_end: datetime,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        await load_raw_occurrences(db_session, uuid4(), range_start, range_end)


async def test_range_service_does_not_query_events_for_another_teacher(
    db_session: AsyncSession,
) -> None:
    teacher = _user("TEACHER", "empty-range-teacher@test.com")
    db_session.add(teacher)
    await db_session.commit()

    results = await load_raw_occurrences(
        db_session,
        teacher.id,
        datetime(2026, 10, 1, 9),
        datetime(2026, 10, 2, 9),
    )

    assert results == []
