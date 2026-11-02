import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.auth.constants import Roles
from backend.auth.models import StudentProfile, User
from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.occurrences import OccurrenceSourceKind
from backend.events.time_utils import normalize_utc, to_storage_utc
from backend.homework.models import Homework
from backend.homework.schemas import HomeworkStatus
from backend.models import Base
from backend.notifications.constants import NotificationType
from backend.notifications.context import get_daily_digest_context
from backend.notifications.models import NotificationIntent
from backend.notifications.schedule_producers import (
    produce_schedule_notifications,
)

HISTORICAL = datetime(2026, 1, 1, tzinfo=UTC)


def _user(
    role: Roles,
    *,
    timezone: str = "UTC",
    teacher_id: uuid.UUID | None = None,
    digest_minute: int | None = None,
    chat_id: int | None = 123456789,
    updated_at: datetime = HISTORICAL,
) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        role=role.value,
        timezone=timezone,
        teacher_id=teacher_id,
        notification_lessons_today=digest_minute,
        telegram_private_chat_id=chat_id,
        is_active=True,
        created_at=HISTORICAL,
        updated_at=updated_at,
        notification_schedule_updated_at=updated_at,
    )


def _student_profile(
    student_id: uuid.UUID,
    *,
    reminder_minutes: int | None = None,
    updated_at: datetime = HISTORICAL,
) -> StudentProfile:
    return StudentProfile(
        student_id=student_id,
        notification_lesson=reminder_minutes,
        notification_homework=2,
        created_at=HISTORICAL,
        updated_at=updated_at,
        notification_lesson_updated_at=updated_at,
        notification_homework_updated_at=updated_at,
    )


def _lesson(
    teacher: User,
    student: User,
    start: datetime,
    *,
    event_id: uuid.UUID | None = None,
) -> Event:
    return Event(
        id=event_id or uuid.uuid4(),
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(start),
        end=to_storage_utc(start + timedelta(minutes=30)),
    )


async def _intents(session: AsyncSession) -> list[NotificationIntent]:
    return list(
        await session.scalars(
            select(NotificationIntent).order_by(NotificationIntent.recipient_id)
        )
    )


@pytest.fixture
async def notification_producer_postgres_session() -> AsyncGenerator[AsyncSession]:
    database_url = os.environ.get("SCHEDULE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SCHEDULE_TEST_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.fail("SCHEDULE_TEST_DATABASE_URL must use postgresql+psycopg")
    schema = f"notification_producer_{uuid.uuid4().hex}"
    engine = create_async_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'
            )
        await engine.dispose()


async def test_daily_digest_is_local_deduplicated_and_skips_empty_days(
    db_session: AsyncSession,
) -> None:
    now = datetime(2026, 11, 1, 10, tzinfo=UTC)
    teacher = _user(Roles.TEACHER, digest_minute=9 * 60)
    student = _user(Roles.STUDENT, teacher_id=teacher.id, digest_minute=9 * 60)
    unroutable = _user(Roles.TEACHER, digest_minute=9 * 60, chat_id=None)
    empty_teacher = _user(Roles.TEACHER, digest_minute=9 * 60)
    db_session.add_all(
        [
            teacher,
            student,
            _student_profile(student.id),
            unroutable,
            empty_teacher,
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            _lesson(teacher, student, datetime(2026, 11, 1, 12, tzinfo=UTC)),
            Event(
                teacher_id=empty_teacher.id,
                user_id=empty_teacher.id,
                event_type=EventType.BREAK.value,
                start=datetime(2026, 11, 1, 12),
                end=datetime(2026, 11, 1, 12, 30),
            ),
        ]
    )
    await db_session.flush()

    await produce_schedule_notifications(db_session, now)
    await db_session.flush()
    await produce_schedule_notifications(db_session, now)

    intents = await _intents(db_session)
    assert len(intents) == 2
    assert {intent.recipient_id for intent in intents} == {teacher.id, student.id}
    assert all(
        intent.notification_type == NotificationType.DAILY_DIGEST
        and intent.source_date == date(2026, 11, 1)
        and normalize_utc(intent.scheduled_at) == now.replace(hour=9)
        for intent in intents
    )


async def test_producer_deduplicates_on_postgresql(
    notification_producer_postgres_session: AsyncSession,
) -> None:
    session = notification_producer_postgres_session
    teacher = _user(Roles.TEACHER, digest_minute=9 * 60)
    student = _user(Roles.STUDENT, teacher_id=teacher.id)
    session.add_all([teacher, student, _student_profile(student.id)])
    await session.flush()
    session.add(_lesson(teacher, student, datetime(2026, 11, 1, 12, tzinfo=UTC)))
    await session.flush()
    now = datetime(2026, 11, 1, 10, tzinfo=UTC)

    await produce_schedule_notifications(session, now)
    await session.commit()
    await produce_schedule_notifications(session, now)

    assert len(await _intents(session)) == 1


async def test_student_digest_context_bulk_loads_only_visible_lesson_homework(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER)
    student = _user(Roles.STUDENT, teacher_id=teacher.id)
    other_student = _user(Roles.STUDENT, teacher_id=teacher.id)
    db_session.add_all([teacher, student, other_student, _student_profile(student.id)])
    await db_session.flush()
    own_lesson = _lesson(
        teacher,
        student,
        datetime(2026, 11, 1, 12, tzinfo=UTC),
    )
    other_lesson = _lesson(
        teacher,
        other_student,
        datetime(2026, 11, 1, 13, tzinfo=UTC),
    )
    db_session.add_all([own_lesson, other_lesson])
    await db_session.flush()
    incomplete = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text="First task",
        lesson_kind="EVENT",
        lesson_source_id=own_lesson.id,
        lesson_occurrence_start=to_storage_utc(datetime(2026, 11, 1, 12, tzinfo=UTC)),
        operation_key=uuid.uuid4(),
    )
    complete = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text="Second task",
        lesson_kind="EVENT",
        lesson_source_id=own_lesson.id,
        lesson_occurrence_start=to_storage_utc(datetime(2026, 11, 1, 12, tzinfo=UTC)),
        status="COMPLETE",
        completion_source="STUDENT_SUBMISSION",
        completed_at=HISTORICAL,
        operation_key=uuid.uuid4(),
    )
    unrelated = Homework(
        teacher_id=teacher.id,
        student_id=other_student.id,
        text="Private task",
        lesson_kind="EVENT",
        lesson_source_id=other_lesson.id,
        lesson_occurrence_start=to_storage_utc(datetime(2026, 11, 1, 13, tzinfo=UTC)),
        operation_key=uuid.uuid4(),
    )
    db_session.add_all([incomplete, complete, unrelated])
    await db_session.flush()

    context = await get_daily_digest_context(
        db_session,
        student,
        date(2026, 11, 1),
        datetime(2026, 11, 1, 11, tzinfo=UTC),
    )

    assert len(context.schedule.days[0].items) == 1
    identity = (
        OccurrenceSourceKind.EVENT,
        own_lesson.id,
        datetime(2026, 11, 1, 12, tzinfo=UTC),
    )
    assert context.homework_status_by_lesson[identity] == (
        HomeworkStatus.INCOMPLETE,
        HomeworkStatus.COMPLETE,
    )
    assert all(
        occurrence_id != other_lesson.id
        for _, occurrence_id, _ in context.homework_status_by_lesson
    )


async def test_digest_dst_gap_and_fold_use_approved_local_resolution(
    db_session: AsyncSession,
) -> None:
    cases = (
        (
            date(2026, 3, 29),
            datetime(2026, 3, 29, 1, 31, tzinfo=UTC),
            datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
            datetime(2026, 3, 29, 4, tzinfo=UTC),
        ),
        (
            date(2026, 10, 25),
            datetime(2026, 10, 25, 0, 31, tzinfo=UTC),
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
            datetime(2026, 10, 25, 3, tzinfo=UTC),
        ),
    )
    expected: dict[uuid.UUID, datetime] = {}
    for _, _, scheduled_at, lesson_start in cases:
        teacher = _user(
            Roles.TEACHER,
            timezone="Europe/Berlin",
            digest_minute=2 * 60 + 30,
        )
        student = _user(
            Roles.STUDENT,
            timezone="Europe/Berlin",
            teacher_id=teacher.id,
        )
        db_session.add_all([teacher, student, _student_profile(student.id)])
        await db_session.flush()
        db_session.add(_lesson(teacher, student, lesson_start))
        await db_session.flush()
        expected[teacher.id] = scheduled_at

    for day, now, _, _ in cases:
        await produce_schedule_notifications(db_session, now)
        intents = await _intents(db_session)
        daily = [
            intent
            for intent in intents
            if intent.notification_type == NotificationType.DAILY_DIGEST
            and intent.source_date == day
        ]
        assert daily
        assert {normalize_utc(intent.scheduled_at) for intent in daily} == {
            expected[intent.recipient_id] for intent in daily
        }


async def test_lesson_reminder_crosses_local_midnight_with_exact_identity(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, timezone="Europe/Berlin")
    student = _user(
        Roles.STUDENT,
        timezone="Europe/Berlin",
        teacher_id=teacher.id,
    )
    db_session.add_all(
        [teacher, student, _student_profile(student.id, reminder_minutes=15)]
    )
    await db_session.flush()
    lesson = _lesson(
        teacher,
        student,
        datetime(2026, 10, 25, 23, 10, tzinfo=UTC),
    )
    lesson.post_break_minutes = 10
    db_session.add(lesson)
    await db_session.flush()

    await produce_schedule_notifications(
        db_session, datetime(2026, 10, 25, 22, 56, tzinfo=UTC)
    )

    intents = await _intents(db_session)
    assert len(intents) == 1
    intent = intents[0]
    assert intent.notification_type == NotificationType.LESSON_REMINDER
    assert intent.occurrence_source_type == "event"
    assert intent.occurrence_source_id == lesson.id
    assert intent.occurrence_start_at is not None
    assert normalize_utc(intent.occurrence_start_at) == datetime(
        2026, 10, 25, 23, 10, tzinfo=UTC
    )
    assert normalize_utc(intent.scheduled_at) == datetime(
        2026, 10, 25, 22, 55, tzinfo=UTC
    )


async def test_open_ended_recurrence_produces_one_deduplicated_reminder(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER)
    student = _user(Roles.STUDENT, teacher_id=teacher.id)
    db_session.add_all(
        [teacher, student, _student_profile(student.id, reminder_minutes=15)]
    )
    await db_session.flush()
    recurrent = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 11, 1, 10),
        end=datetime(2026, 11, 1, 10, 30),
        interval=7,
        interval_end=None,
        recurrence_mode=RecurrenceMode.TEACHER_LOCAL.value,
        recurrence_timezone="UTC",
        post_break_minutes=0,
    )
    db_session.add(recurrent)
    await db_session.flush()
    now = datetime(2026, 11, 1, 9, 45, tzinfo=UTC)

    await produce_schedule_notifications(db_session, now)
    await db_session.commit()
    await produce_schedule_notifications(db_session, now)

    intents = await _intents(db_session)
    assert len(intents) == 1
    assert intents[0].occurrence_source_type == "recurrent_event"
    assert intents[0].occurrence_source_id == recurrent.id
    assert intents[0].occurrence_start_at is not None
    assert normalize_utc(intents[0].occurrence_start_at) == datetime(
        2026, 11, 1, 10, tzinfo=UTC
    )


async def test_canceled_vacation_hidden_and_moved_lessons_do_not_create_reminders(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER)
    canceled_student = _user(Roles.STUDENT, teacher_id=teacher.id)
    hidden_student = _user(Roles.STUDENT, teacher_id=teacher.id)
    moved_student = _user(Roles.STUDENT, teacher_id=teacher.id)
    disabled_student = _user(Roles.STUDENT, teacher_id=teacher.id)
    db_session.add_all(
        [
            teacher,
            canceled_student,
            hidden_student,
            moved_student,
            disabled_student,
            _student_profile(canceled_student.id, reminder_minutes=15),
            _student_profile(hidden_student.id, reminder_minutes=15),
            _student_profile(moved_student.id, reminder_minutes=15),
            _student_profile(disabled_student.id),
        ]
    )
    await db_session.flush()
    recurrent = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=canceled_student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 11, 1, 10),
        end=datetime(2026, 11, 1, 10, 30),
        interval=7,
        interval_end=None,
        recurrence_mode=RecurrenceMode.UTC_STEP.value,
        recurrence_timezone=None,
        post_break_minutes=0,
    )
    canceled_start = datetime(2026, 11, 1, 10)
    db_session.add_all(
        [
            recurrent,
            _lesson(
                teacher,
                hidden_student,
                datetime(2026, 11, 1, 10, tzinfo=UTC),
            ),
            Event(
                teacher_id=teacher.id,
                user_id=hidden_student.id,
                event_type=EventType.VACATION.value,
                start=datetime(2026, 11, 1, 9, 59),
                end=datetime(2026, 11, 1, 10, 31),
            ),
            _lesson(
                teacher,
                moved_student,
                datetime(2026, 11, 1, 10, 30, tzinfo=UTC),
            ),
            _lesson(
                teacher,
                disabled_student,
                datetime(2026, 11, 1, 10, tzinfo=UTC),
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        RecurrentCancels(
            recurrent_event_id=recurrent.id,
            start=canceled_start,
            end=datetime(2026, 11, 1, 10, 30),
        )
    )
    await db_session.flush()

    await produce_schedule_notifications(
        db_session, datetime(2026, 11, 1, 9, 45, tzinfo=UTC)
    )

    assert await _intents(db_session) == []


async def test_late_preferences_and_late_telegram_link_do_not_backfill(
    db_session: AsyncSession,
) -> None:
    due = datetime(2026, 11, 1, 9, tzinfo=UTC)
    now = datetime(2026, 11, 1, 10, tzinfo=UTC)
    late_teacher = _user(
        Roles.TEACHER,
        digest_minute=9 * 60,
        updated_at=datetime(2026, 11, 1, 9, 1, tzinfo=UTC),
    )
    linked_late_teacher = _user(
        Roles.TEACHER,
        digest_minute=9 * 60,
        updated_at=datetime(2026, 11, 1, 9, 1, tzinfo=UTC),
    )
    unrelated_late_teacher = _user(
        Roles.TEACHER,
        digest_minute=9 * 60,
        updated_at=datetime(2026, 11, 1, 9, 1, tzinfo=UTC),
    )
    unrelated_late_teacher.notification_schedule_updated_at = HISTORICAL
    no_route_teacher = _user(
        Roles.TEACHER,
        digest_minute=9 * 60,
        chat_id=None,
    )
    db_session.add_all(
        [
            late_teacher,
            linked_late_teacher,
            unrelated_late_teacher,
            no_route_teacher,
        ]
    )
    await db_session.flush()
    for teacher in (
        late_teacher,
        linked_late_teacher,
        unrelated_late_teacher,
        no_route_teacher,
    ):
        db_session.add(_lesson(teacher, teacher, datetime(2026, 11, 1, 12, tzinfo=UTC)))
    await db_session.flush()
    linked_late_teacher.telegram_private_chat_id = 987654321
    linked_late_teacher.updated_at = due + timedelta(minutes=1)
    await db_session.flush()

    await produce_schedule_notifications(db_session, now)

    intents = await _intents(db_session)
    assert len(intents) == 1
    assert intents[0].recipient_id == unrelated_late_teacher.id
