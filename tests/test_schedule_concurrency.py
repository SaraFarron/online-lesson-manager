import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime

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

from backend.auth.models import User
from backend.constants import EventType
from backend.events.models import Event, TeacherScheduleGuard
from backend.events.schemas import (
    EventCreate,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventUpdate,
)
from backend.events.service import (
    _run_serialized_schedule_mutation,
    create_event,
    create_recurrent_event,
    delete_event,
    update_event,
    update_recurrent_event,
)
from backend.events.validators import EventValidator
from backend.exceptions import Conflict, ValidationError
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
            _run_serialized_schedule_mutation(first_session, teacher_id, hold_guard)
        )
        await asyncio.wait_for(first_locked.wait(), timeout=2)
        await second_session.scalar(select(func.count()).select_from(User))

        with pytest.raises(Conflict, match="retry this request") as exc_info:
            await _run_serialized_schedule_mutation(
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
            await _run_serialized_schedule_mutation(
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
