import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.auth.constants import Roles
from backend.auth.models import StudentProfile, User
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.time_utils import normalize_utc, to_storage_utc
from backend.homework.models import Homework
from backend.models import Base
from backend.notifications.constants import NotificationState, NotificationType
from backend.notifications.homework_producers import produce_homework_reminders
from backend.notifications.models import NotificationIntent
from backend.notifications.service import claim_pending_intents

HISTORICAL = datetime(2026, 1, 1, tzinfo=UTC)
RUN_AT = datetime(2026, 11, 1, 9, 5, tzinfo=UTC)
LESSON_START = datetime(2026, 11, 3, 12, tzinfo=UTC)


@pytest.fixture
async def homework_notification_postgres_session() -> AsyncGenerator[AsyncSession]:
    database_url = os.environ.get("SCHEDULE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SCHEDULE_TEST_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.fail("SCHEDULE_TEST_DATABASE_URL must use postgresql+psycopg")
    schema = f"homework_notification_{uuid.uuid4().hex}"
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


def _user(
    role: Roles,
    *,
    teacher_id: uuid.UUID | None = None,
    chat_id: int | None = 123456789,
    digest_minute: int | None = 9 * 60,
    schedule_updated_at: datetime = HISTORICAL,
) -> User:
    user_id = uuid.uuid4()
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        role=role.value,
        timezone="UTC",
        teacher_id=teacher_id,
        is_active=True,
        telegram_private_chat_id=chat_id,
        notification_lessons_today=digest_minute,
        notification_schedule_updated_at=schedule_updated_at,
        created_at=HISTORICAL,
        updated_at=HISTORICAL,
    )


def _student_profile(
    student_id: uuid.UUID,
    *,
    homework_days: int | None = 2,
    updated_at: datetime = HISTORICAL,
) -> StudentProfile:
    return StudentProfile(
        student_id=student_id,
        notification_lesson=15,
        notification_lesson_updated_at=HISTORICAL,
        notification_homework=homework_days,
        notification_homework_updated_at=updated_at,
        created_at=HISTORICAL,
        updated_at=HISTORICAL,
    )


def _lesson(teacher: User, student: User, start: datetime) -> Event:
    return Event(
        id=uuid.uuid4(),
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(start),
        end=to_storage_utc(start + timedelta(minutes=30)),
    )


def _homework(
    teacher: User,
    student: User,
    lesson: Event | RecurrentEvent,
    *,
    status: str = "INCOMPLETE",
    deleted: bool = False,
) -> Homework:
    start = lesson.start
    homework = Homework(
        id=uuid.uuid4(),
        teacher_id=teacher.id,
        student_id=student.id,
        text="Complete the exercise",
        lesson_kind="EVENT" if isinstance(lesson, Event) else "RECURRENT_EVENT",
        lesson_source_id=lesson.id,
        lesson_occurrence_start=to_storage_utc(normalize_utc(start)),
        status=status,
        completion_source="TEACHER" if status == "COMPLETE" else None,
        completed_at=HISTORICAL if status == "COMPLETE" else None,
        operation_key=uuid.uuid4(),
        deleted_at=HISTORICAL if deleted else None,
        cleanup_pending=False,
    )
    return homework


async def test_homework_reminder_producer_deduplicates_on_postgresql(
    homework_notification_postgres_session: AsyncSession,
) -> None:
    session = homework_notification_postgres_session
    teacher = _user(Roles.TEACHER, chat_id=None, digest_minute=None)
    student = _user(Roles.STUDENT, teacher_id=teacher.id)
    session.add_all([teacher, student, _student_profile(student.id)])
    await session.flush()
    lesson = _lesson(teacher, student, LESSON_START)
    session.add(lesson)
    await session.flush()
    session.add(_homework(teacher, student, lesson))
    await session.flush()

    await produce_homework_reminders(session, RUN_AT)
    await produce_homework_reminders(session, RUN_AT)
    await session.commit()

    intents = list(await session.scalars(select(NotificationIntent)))
    assert len(intents) == 1
    assert intents[0].notification_type == NotificationType.HOMEWORK_REMINDER
    assert intents[0].source_id is not None


@pytest.mark.asyncio
async def test_due_homework_reminders_require_exact_visible_incomplete_lesson(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, chat_id=None, digest_minute=None)
    visible = _user(Roles.STUDENT, teacher_id=teacher.id)
    canceled = _user(Roles.STUDENT, teacher_id=teacher.id)
    hidden = _user(Roles.STUDENT, teacher_id=teacher.id)
    completed = _user(Roles.STUDENT, teacher_id=teacher.id)
    deleted = _user(Roles.STUDENT, teacher_id=teacher.id)
    late_setting = _user(Roles.STUDENT, teacher_id=teacher.id)
    delinked = _user(Roles.STUDENT, teacher_id=teacher.id)
    no_route = _user(Roles.STUDENT, teacher_id=teacher.id, chat_id=None)
    delinked.teacher_id = None
    students = [
        visible,
        canceled,
        hidden,
        completed,
        deleted,
        late_setting,
        delinked,
        no_route,
    ]
    profiles = [
        _student_profile(student.id)
        for student in students
        if student is not late_setting
    ]
    profiles.append(
        _student_profile(
            late_setting.id,
            updated_at=datetime(2026, 11, 1, 9, 1, tzinfo=UTC),
        )
    )
    db_session.add_all([teacher, *students, *profiles])
    await db_session.flush()

    visible_lesson = _lesson(teacher, visible, LESSON_START)
    hidden_lesson = _lesson(teacher, hidden, LESSON_START)
    completed_lesson = _lesson(teacher, completed, LESSON_START)
    deleted_lesson = _lesson(teacher, deleted, LESSON_START)
    late_lesson = _lesson(teacher, late_setting, LESSON_START)
    delinked_lesson = _lesson(teacher, delinked, LESSON_START)
    no_route_lesson = _lesson(teacher, no_route, LESSON_START)
    canceled_lesson = RecurrentEvent(
        id=uuid.uuid4(),
        teacher_id=teacher.id,
        user_id=canceled.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(LESSON_START),
        end=to_storage_utc(LESSON_START + timedelta(minutes=30)),
        interval=7,
        interval_end=None,
        recurrence_mode="UTC_STEP",
        recurrence_timezone=None,
        post_break_minutes=0,
    )
    db_session.add_all(
        [
            visible_lesson,
            hidden_lesson,
            completed_lesson,
            deleted_lesson,
            late_lesson,
            delinked_lesson,
            no_route_lesson,
            canceled_lesson,
            Event(
                teacher_id=teacher.id,
                user_id=hidden.id,
                event_type=EventType.VACATION.value,
                start=to_storage_utc(LESSON_START - timedelta(minutes=1)),
                end=to_storage_utc(LESSON_START + timedelta(minutes=31)),
            ),
            RecurrentCancels(
                recurrent_event_id=canceled_lesson.id,
                start=to_storage_utc(LESSON_START),
                end=to_storage_utc(LESSON_START + timedelta(minutes=30)),
            ),
        ]
    )
    await db_session.flush()
    homeworks = [
        _homework(teacher, visible, visible_lesson),
        _homework(teacher, canceled, canceled_lesson),
        _homework(teacher, hidden, hidden_lesson),
        _homework(teacher, completed, completed_lesson, status="COMPLETE"),
        _homework(teacher, deleted, deleted_lesson, deleted=True),
        _homework(teacher, late_setting, late_lesson),
        _homework(teacher, delinked, delinked_lesson),
        _homework(teacher, no_route, no_route_lesson),
    ]
    db_session.add_all(homeworks)
    await db_session.flush()

    await produce_homework_reminders(db_session, RUN_AT)
    await produce_homework_reminders(db_session, RUN_AT)

    intents = list(await db_session.scalars(select(NotificationIntent)))
    assert len(intents) == 1
    intent = intents[0]
    assert intent.notification_type == NotificationType.HOMEWORK_REMINDER
    assert intent.source_id == homeworks[0].id
    assert intent.source_date == RUN_AT.date()
    assert intent.occurrence_source_type == "event"
    assert intent.occurrence_source_id == visible_lesson.id
    assert intent.occurrence_start_at is not None
    assert normalize_utc(intent.occurrence_start_at) == LESSON_START
    assert normalize_utc(intent.scheduled_at) == RUN_AT.replace(minute=0)

    homeworks[0].status = "COMPLETE"
    homeworks[0].completion_source = "TEACHER"
    homeworks[0].completed_at = RUN_AT
    await db_session.commit()
    assert (
        await claim_pending_intents(db_session, now=RUN_AT + timedelta(minutes=1)) == []
    )
    await db_session.refresh(intent)
    assert intent.state == NotificationState.SUPPRESSED


async def test_reminder_is_suppressed_when_lead_preference_changes_after_due_time(
    db_session: AsyncSession,
) -> None:
    teacher = _user(Roles.TEACHER, chat_id=None, digest_minute=None)
    student = _user(Roles.STUDENT, teacher_id=teacher.id)
    profile = _student_profile(student.id)
    db_session.add_all([teacher, student, profile])
    await db_session.flush()
    lesson = _lesson(teacher, student, LESSON_START)
    db_session.add(lesson)
    await db_session.flush()
    homework = _homework(teacher, student, lesson)
    db_session.add(homework)
    await db_session.flush()
    await produce_homework_reminders(db_session, RUN_AT)

    profile.notification_homework = 3
    profile.notification_homework_updated_at = RUN_AT + timedelta(minutes=1)
    await db_session.commit()
    claimed = await claim_pending_intents(db_session, now=RUN_AT + timedelta(minutes=2))

    intent = await db_session.scalar(select(NotificationIntent))
    assert claimed == []
    assert intent is not None
    assert intent.state == NotificationState.SUPPRESSED
    assert intent.last_error_code == "homework_reminder_preference_changed"
