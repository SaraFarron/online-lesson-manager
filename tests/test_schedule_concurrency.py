import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, time

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backend.auth.models import TeacherProfile, User
from backend.auth.schemas import TeacherProfileUpdate
from backend.auth.service import get_user_by_id, update_teacher_profile
from backend.constants import EventType
from backend.events.models import (
    Event,
    RecurrentCancels,
    RecurrentEvent,
    TeacherScheduleGuard,
)
from backend.events.schemas import (
    EventCreate,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventUpdate,
)
from backend.events.service import (
    cancel_recurrent_lesson_occurrence,
    create_event,
    create_recurrent_event,
    delete_event,
    run_serialized_schedule_mutation,
    update_event,
    update_recurrent_event,
)
from backend.events.validators import EventValidator
from backend.exceptions import Conflict, ValidationError
from backend.lessons.schemas import RecurrentOccurrenceCancelRequest
from backend.models import Base


async def _create_shared_memory_database() -> tuple[
    AsyncEngine, AsyncConnection, async_sessionmaker[AsyncSession]
]:
    database_name = f"schedule_guard_{uuid.uuid4().hex}"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:{database_name}?mode=memory&cache=shared&uri=true",
        poolclass=NullPool,
    )
    keeper = await engine.connect()
    await keeper.run_sync(Base.metadata.create_all)
    return engine, keeper, async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(params=("sqlite", "postgresql"))
async def mutation_session_factory(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    if request.param == "sqlite":
        engine, keeper, session_factory = await _create_shared_memory_database()
        try:
            yield session_factory
        finally:
            await keeper.close()
            await engine.dispose()
        return

    database_url = os.environ.get("SCHEDULE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SCHEDULE_TEST_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.fail("SCHEDULE_TEST_DATABASE_URL must use postgresql+psycopg")

    schema = f"schedule_test_{uuid.uuid4().hex}"
    engine = create_async_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_guard_rejects_concurrent_mutation_without_running_it() -> None:
    engine, keeper, session_factory = await _create_shared_memory_database()
    teacher_id = uuid.uuid4()
    first_session = session_factory()
    second_session = session_factory()
    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    second_operation_called = False

    async def hold_guard() -> None:
        first_locked.set()
        await release_first.wait()
        await first_session.commit()

    async def should_not_run() -> None:
        nonlocal second_operation_called
        second_operation_called = True

    try:
        first_task = asyncio.create_task(
            run_serialized_schedule_mutation(first_session, teacher_id, hold_guard)
        )
        await asyncio.wait_for(first_locked.wait(), timeout=2)
        await second_session.scalar(select(func.count()).select_from(User))

        with pytest.raises(Conflict, match="retry this request") as exc_info:
            await run_serialized_schedule_mutation(
                second_session, teacher_id, should_not_run
            )

        assert exc_info.value.status_code == 409
        assert not second_operation_called
        release_first.set()
        await first_task

        guard_count = await second_session.scalar(
            select(func.count()).select_from(TeacherScheduleGuard)
        )
        assert guard_count == 1
    finally:
        release_first.set()
        await first_session.close()
        await second_session.close()
        await keeper.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_break_preference_update_serializes_with_booking_policy() -> None:
    engine, keeper, session_factory = await _create_shared_memory_database()
    teacher_id = uuid.uuid4()
    first_session = session_factory()
    second_session = session_factory()
    guard_acquired = asyncio.Event()
    release_guard = asyncio.Event()

    try:
        teacher = User(id=teacher_id, role="TEACHER", timezone="UTC")
        student = User(role="STUDENT", timezone="UTC", teacher_id=teacher_id)
        first_session.add_all(
            [
                teacher,
                student,
                TeacherProfile(
                    teacher_id=teacher_id,
                    code=f"t_{uuid.uuid4().hex[:8]}",
                    work_start=time(9),
                    work_end=time(18),
                    working_days=[1, 2, 3, 4, 5, 6, 7],
                    lesson_length=60,
                    between_lessons_break=5,
                    max_consecutive_lessons=4,
                    max_lessons_per_day=6,
                ),
            ]
        )
        await first_session.commit()

        async def hold_guard() -> None:
            guard_acquired.set()
            await release_guard.wait()
            await first_session.commit()

        guard_task = asyncio.create_task(
            run_serialized_schedule_mutation(first_session, teacher_id, hold_guard)
        )
        await asyncio.wait_for(guard_acquired.wait(), timeout=2)

        stale_user = await get_user_by_id(second_session, teacher_id)
        assert stale_user is not None
        with pytest.raises(Conflict, match="retry this request"):
            await update_teacher_profile(
                second_session,
                TeacherProfileUpdate(between_lessons_break=0),
                stale_user,
            )

        release_guard.set()
        await guard_task
        await second_session.rollback()
        current_user = await get_user_by_id(second_session, teacher_id)
        assert current_user is not None
        assert current_user.teacher_profile is not None
        assert current_user.teacher_profile.between_lessons_break == 5
        assert current_user.teacher_profile.work_start == time(9)
        updated = await update_teacher_profile(
            second_session,
            TeacherProfileUpdate(between_lessons_break=0),
            current_user,
        )
        assert updated.between_lessons_break == 0
        assert updated.work_start == time(9)
        assert updated.max_consecutive_lessons == 4

        event = await create_event(
            second_session,
            EventCreate(
                start=datetime(2026, 10, 26, 9),
                end=datetime(2026, 10, 26, 10),
                user_id=student.id,
                event_type=EventType.LESSON,
            ),
            EventValidator(),
            teacher_id=teacher_id,
        )
        assert event.post_break_minutes == 0
    finally:
        release_guard.set()
        await first_session.close()
        await second_session.close()
        await keeper.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_schedule_mutation_rolls_back_guard_and_event() -> None:
    engine, keeper, session_factory = await _create_shared_memory_database()
    session = session_factory()
    teacher_id = uuid.uuid4()
    now = datetime(2026, 10, 23, 9)
    try:
        session.add(User(id=teacher_id, role="TEACHER", timezone="UTC"))
        await session.commit()

        async def fail_after_write() -> None:
            session.add(
                Event(
                    teacher_id=teacher_id,
                    user_id=teacher_id,
                    event_type="LESSON",
                    start=now,
                    end=datetime(2026, 10, 23, 10),
                )
            )
            await session.flush()
            raise RuntimeError("simulated failed mutation")

        with pytest.raises(RuntimeError, match="simulated failed mutation"):
            await run_serialized_schedule_mutation(
                session, teacher_id, fail_after_write
            )

        assert await session.scalar(select(func.count()).select_from(Event)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(TeacherScheduleGuard))
            == 0
        )
    finally:
        await session.close()
        await keeper.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_event_service_acquires_guard_before_validation(
    db_session: AsyncSession,
) -> None:
    teacher = User(role="TEACHER", timezone="UTC")
    db_session.add(teacher)
    await db_session.commit()

    event_data = EventCreate(
        start=datetime(2026, 10, 23, 9),
        end=datetime(2026, 10, 23, 10),
        user_id=teacher.id,
        event_type=EventType.LESSON,
    )
    event = await create_event(
        db_session, event_data, EventValidator(), teacher_id=teacher.id
    )

    guard = await db_session.get(TeacherScheduleGuard, teacher.id)
    assert guard is not None
    assert event.teacher_id == teacher.id


@pytest.mark.asyncio
async def test_all_event_mutation_entrypoints_acquire_the_guard(
    db_session: AsyncSession,
) -> None:
    teacher = User(role="TEACHER", timezone="UTC")
    db_session.add(teacher)
    await db_session.flush()
    student = User(role="STUDENT", timezone="UTC", teacher_id=teacher.id)
    db_session.add(student)
    await db_session.commit()

    async def assert_guard_was_acquired_and_clear_it() -> None:
        guard_count = await db_session.scalar(
            select(func.count())
            .select_from(TeacherScheduleGuard)
            .where(TeacherScheduleGuard.teacher_id == teacher.id)
        )
        assert guard_count == 1
        await db_session.execute(
            delete(TeacherScheduleGuard).where(
                TeacherScheduleGuard.teacher_id == teacher.id
            )
        )
        await db_session.commit()

    validator = EventValidator()
    event = await create_event(
        db_session,
        EventCreate(
            start=datetime(2026, 10, 23, 9),
            end=datetime(2026, 10, 23, 10),
            user_id=student.id,
            event_type=EventType.LESSON,
        ),
        validator,
        teacher_id=teacher.id,
    )
    await assert_guard_was_acquired_and_clear_it()

    event = await update_event(
        db_session,
        event,
        EventUpdate(
            start=datetime(2026, 10, 23, 10),
            end=datetime(2026, 10, 23, 11),
            user_id=student.id,
        ),
        validator,
        teacher_id=teacher.id,
    )
    await assert_guard_was_acquired_and_clear_it()
    await delete_event(db_session, event)
    await assert_guard_was_acquired_and_clear_it()

    recurrent_event = await create_recurrent_event(
        db_session,
        RecurrentEventCreate(
            start=datetime(2026, 10, 26, 9),
            end=datetime(2026, 10, 26, 10),
            interval=7,
            interval_end=datetime(2026, 10, 27, 9),
            user_id=student.id,
            event_type=EventType.LESSON,
        ),
        validator,
        teacher_id=teacher.id,
    )
    await assert_guard_was_acquired_and_clear_it()

    recurrent_event = await update_recurrent_event(
        db_session,
        recurrent_event,
        RecurrentEventUpdate(
            start=datetime(2026, 10, 26, 9),
            end=datetime(2026, 10, 26, 10),
            interval=7,
            interval_end=datetime(2026, 10, 27, 9),
            user_id=student.id,
        ),
        validator,
        teacher_id=teacher.id,
    )
    await assert_guard_was_acquired_and_clear_it()
    await delete_event(db_session, recurrent_event)
    await assert_guard_was_acquired_and_clear_it()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type", [EventType.LESSON, EventType.BREAK, EventType.VACATION]
)
async def test_concurrent_conflicting_event_mutations_are_serialized(
    mutation_session_factory: async_sessionmaker[AsyncSession],
    event_type: EventType,
) -> None:
    async with mutation_session_factory() as seed_session:
        teacher = User(role="TEACHER", timezone="UTC")
        seed_session.add(teacher)
        await seed_session.flush()
        student = User(role="STUDENT", timezone="UTC", teacher_id=teacher.id)
        seed_session.add(student)
        await seed_session.flush()
        await seed_session.commit()

    event_data = EventCreate(
        start=datetime(2026, 10, 26, 9),
        end=datetime(2026, 10, 26, 10),
        user_id=student.id if event_type == EventType.LESSON else teacher.id,
        event_type=event_type,
    )
    validator = EventValidator(expected_event_type=event_type)
    start_together = asyncio.Barrier(2)

    async def attempt_mutation() -> Event | Conflict | ValidationError:
        await start_together.wait()
        async with mutation_session_factory() as session:
            try:
                return await create_event(
                    session,
                    event_data.model_copy(),
                    validator,
                    teacher_id=teacher.id,
                    actor_is_teacher=True,
                )
            except (Conflict, ValidationError) as exc:
                return exc

    outcomes = await asyncio.gather(attempt_mutation(), attempt_mutation())
    created = [outcome for outcome in outcomes if isinstance(outcome, Event)]
    rejected = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, (Conflict, ValidationError))
    ]
    assert len(created) == 1
    assert len(rejected) == 1

    async with mutation_session_factory() as verification_session:
        events = (
            await verification_session.scalars(
                select(Event).where(Event.teacher_id == teacher.id)
            )
        ).all()
        guard = await verification_session.get(TeacherScheduleGuard, teacher.id)
    assert len(events) == 1
    assert events[0].event_type == event_type.value
    assert guard is not None


@pytest.mark.asyncio
async def test_concurrent_lesson_and_reserved_break_booking_are_serialized(
    mutation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with mutation_session_factory() as seed_session:
        teacher = User(role="TEACHER", timezone="UTC")
        seed_session.add(teacher)
        await seed_session.flush()
        student = User(role="STUDENT", timezone="UTC", teacher_id=teacher.id)
        seed_session.add(student)
        await seed_session.commit()

    validator = EventValidator(expected_event_type=EventType.LESSON)
    async with mutation_session_factory() as seed_session:
        moving_lesson = await create_event(
            seed_session,
            EventCreate(
                start=datetime(2026, 10, 26, 12),
                end=datetime(2026, 10, 26, 13),
                user_id=student.id,
                event_type=EventType.LESSON,
            ),
            validator,
            teacher_id=teacher.id,
            actor_is_teacher=True,
        )

    parent_lesson = EventCreate(
        start=datetime(2026, 10, 26, 9),
        end=datetime(2026, 10, 26, 10),
        user_id=student.id,
        event_type=EventType.LESSON,
    )
    start_together = asyncio.Barrier(2)

    async def attempt_create() -> Event | Conflict | ValidationError:
        await start_together.wait()
        async with mutation_session_factory() as session:
            try:
                return await create_event(
                    session,
                    parent_lesson,
                    validator,
                    teacher_id=teacher.id,
                    actor_is_teacher=True,
                )
            except (Conflict, ValidationError) as exc:
                return exc

    async def attempt_move() -> Event | Conflict | ValidationError:
        await start_together.wait()
        async with mutation_session_factory() as session:
            current = await session.get(Event, moving_lesson.id)
            assert current is not None
            try:
                return await update_event(
                    session,
                    current,
                    EventUpdate(
                        start=datetime(2026, 10, 26, 10, 4),
                        end=datetime(2026, 10, 26, 11, 4),
                        user_id=student.id,
                    ),
                    validator,
                    teacher_id=teacher.id,
                    actor_is_teacher=True,
                )
            except (Conflict, ValidationError) as exc:
                return exc

    outcomes = await asyncio.gather(attempt_create(), attempt_move())
    assert sum(isinstance(outcome, Event) for outcome in outcomes) == 1
    assert (
        sum(isinstance(outcome, (Conflict, ValidationError)) for outcome in outcomes)
        == 1
    )

    async with mutation_session_factory() as verification_session:
        events = (
            await verification_session.scalars(
                select(Event).where(Event.teacher_id == teacher.id)
            )
        ).all()
    if len(events) == 2:
        assert sorted(event.start for event in events) == [
            datetime(2026, 10, 26, 9),
            datetime(2026, 10, 26, 12),
        ]
    else:
        assert len(events) == 1
        assert events[0].start == datetime(2026, 10, 26, 10, 4)


@pytest.mark.asyncio
async def test_concurrent_occurrence_cancellations_preserve_both_identities(
    mutation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with mutation_session_factory() as seed_session:
        teacher = User(role="TEACHER", timezone="UTC")
        seed_session.add(teacher)
        await seed_session.flush()
        student = User(role="STUDENT", timezone="UTC", teacher_id=teacher.id)
        seed_session.add(student)
        await seed_session.flush()
        series = RecurrentEvent(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type=EventType.LESSON.value,
            start=datetime(2026, 12, 1, 9),
            end=datetime(2026, 12, 1, 10),
            interval=7,
            interval_end=datetime(2026, 12, 22, 9),
            recurrence_mode="UTC_STEP",
            recurrence_timezone=None,
        )
        seed_session.add(series)
        await seed_session.commit()
        teacher_id = teacher.id
        series_id = series.id
        actor = User(id=teacher.id, role="TEACHER", timezone="UTC")

    start_together = asyncio.Barrier(2)
    starts = (
        datetime(2026, 12, 8, 9),
        datetime(2026, 12, 15, 9),
    )

    async def cancel(start: datetime) -> Conflict | None:
        await start_together.wait()
        async with mutation_session_factory() as session:
            try:
                await cancel_recurrent_lesson_occurrence(
                    session,
                    series_id,
                    RecurrentOccurrenceCancelRequest(
                        occurrence_start=start,
                        occurrence_end=start.replace(hour=10),
                    ),
                    actor,
                    teacher_id,
                )
            except Conflict as exc:
                return exc
        return None

    outcomes = await asyncio.gather(*(cancel(start) for start in starts))
    for start, outcome in zip(starts, outcomes, strict=True):
        if outcome is None:
            continue
        assert outcome.status_code == 409
        async with mutation_session_factory() as session:
            await cancel_recurrent_lesson_occurrence(
                session,
                series_id,
                RecurrentOccurrenceCancelRequest(
                    occurrence_start=start,
                    occurrence_end=start.replace(hour=10),
                ),
                actor,
                teacher_id,
            )

    async with mutation_session_factory() as verification_session:
        cancellations = (
            await verification_session.scalars(
                select(RecurrentCancels)
                .where(RecurrentCancels.recurrent_event_id == series_id)
                .order_by(RecurrentCancels.start)
            )
        ).all()
    assert [cancellation.start for cancellation in cancellations] == [
        datetime(2026, 12, 8, 9),
        datetime(2026, 12, 15, 9),
    ]
