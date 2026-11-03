import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backend.activity.constants import StudentActionType
from backend.activity.models import StudentAction
from backend.auth import service as auth_service
from backend.auth.exceptions import StudentAlreadyLinked
from backend.auth.models import (
    StudentProfile,
    StudentRemovalOperation,
    StudentTeacherRelationship,
    TeacherProfile,
    User,
)
from backend.auth.removal import confirm_student_removal, preview_student_removal
from backend.auth.schemas import StudentRemovalConfirm
from backend.auth.utils import create_access_token
from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.mutations.one_off import create_event
from backend.events.schemas import EventCreate
from backend.events.time_utils import to_storage_utc
from backend.events.validators import EventValidator
from backend.exceptions import Conflict, Forbidden, NotFound
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.homework.schemas import HomeworkSubmissionCreate
from backend.homework.services.submissions import create_submission
from backend.main import app
from backend.models import Base
from backend.notifications.constants import NotificationState, NotificationType
from backend.notifications.models import NotificationIntent


@pytest.fixture(params=("sqlite", "postgresql"))
async def removal_session_factory(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    if request.param == "sqlite":
        database_name = f"student_removal_{uuid.uuid4().hex}"
        engine = create_async_engine(
            "sqlite+aiosqlite:///file:"
            f"{database_name}?mode=memory&cache=shared&uri=true",
            poolclass=NullPool,
        )
        keeper = await engine.connect()
        await keeper.run_sync(Base.metadata.create_all)
        try:
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await keeper.close()
            await engine.dispose()
        return

    database_url = os.environ.get("SCHEDULE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SCHEDULE_TEST_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.fail("SCHEDULE_TEST_DATABASE_URL must use postgresql+psycopg")

    schema = f"student_removal_{uuid.uuid4().hex}"
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


@pytest.fixture
def homework_storage(tmp_path: Path) -> Generator[Path]:
    config = HomeworkConfig(STORAGE_PATH=tmp_path)
    app.dependency_overrides[get_homework_settings] = lambda: config
    try:
        yield tmp_path
    finally:
        app.dependency_overrides.pop(get_homework_settings, None)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _linked_users(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[User, User, StudentTeacherRelationship]:
    teacher = User(role="TEACHER", timezone="UTC", full_name="Teacher")
    session.add(teacher)
    await session.flush()
    student = User(
        role="STUDENT",
        timezone="UTC",
        full_name="Student",
        teacher_id=teacher.id,
    )
    session.add_all([teacher, student])
    await session.flush()
    relationship = StudentTeacherRelationship(
        teacher_id=teacher.id,
        student_id=student.id,
        linked_at=now - timedelta(days=30),
    )
    session.add_all(
        [
            relationship,
            TeacherProfile(
                teacher_id=teacher.id,
                code=f"REMOVE{uuid.uuid4().hex[:8].upper()}",
            ),
            StudentProfile(
                student_id=student.id,
                notification_lesson=15,
                notification_homework=2,
            ),
        ]
    )
    await session.flush()
    return teacher, student, relationship


def _intent(
    recipient_id: uuid.UUID,
    notification_type: NotificationType,
    dedupe_key: str,
    now: datetime,
    *,
    source_id: uuid.UUID | None = None,
    occurrence_source_type: str | None = None,
    occurrence_source_id: uuid.UUID | None = None,
    state: NotificationState = NotificationState.PENDING,
) -> NotificationIntent:
    return NotificationIntent(
        recipient_id=recipient_id,
        notification_type=notification_type.value,
        dedupe_key=dedupe_key,
        source_id=source_id,
        occurrence_source_type=occurrence_source_type,
        occurrence_source_id=occurrence_source_id,
        scheduled_at=now,
        available_at=now,
        expires_at=now + timedelta(days=1),
        state=state.value,
        sent_at=now if state == NotificationState.SENT else None,
    )


async def test_confirm_removal_retains_history_and_homework_and_retries_safely(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    teacher, student, relationship = await _linked_users(db_session, now=now)
    other_student = User(
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
    )
    db_session.add(other_student)
    await db_session.flush()
    db_session.add(
        StudentTeacherRelationship(
            teacher_id=teacher.id,
            student_id=other_student.id,
            linked_at=now - timedelta(days=10),
        )
    )

    past_lesson = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(now - timedelta(days=2)),
        end=to_storage_utc(now - timedelta(days=2) + timedelta(hours=1)),
    )
    future_lesson = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(now + timedelta(days=1)),
        end=to_storage_utc(now + timedelta(days=1, hours=1)),
    )
    unrelated_lesson = Event(
        teacher_id=teacher.id,
        user_id=other_student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(now + timedelta(days=1)),
        end=to_storage_utc(now + timedelta(days=1, hours=1)),
    )
    recurring = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(now - timedelta(days=14)),
        end=to_storage_utc(now - timedelta(days=14) + timedelta(hours=1)),
        interval=7,
        interval_end=to_storage_utc(now + timedelta(days=14)),
        recurrence_mode=RecurrenceMode.UTC_STEP.value,
        recurrence_timezone=None,
    )
    future_series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(now + timedelta(days=7)),
        end=to_storage_utc(now + timedelta(days=7, hours=1)),
        interval=7,
        interval_end=to_storage_utc(now + timedelta(days=28)),
        recurrence_mode=RecurrenceMode.UTC_STEP.value,
        recurrence_timezone=None,
    )
    db_session.add_all(
        [
            past_lesson,
            future_lesson,
            unrelated_lesson,
            recurring,
            future_series,
        ]
    )
    await db_session.flush()
    past_cancel = RecurrentCancels(
        recurrent_event_id=recurring.id,
        start=to_storage_utc(now - timedelta(days=14)),
        end=to_storage_utc(now - timedelta(days=14) + timedelta(hours=1)),
    )
    future_cancel = RecurrentCancels(
        recurrent_event_id=recurring.id,
        start=to_storage_utc(now + timedelta(days=7)),
        end=to_storage_utc(now + timedelta(days=7, hours=1)),
    )

    homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text="Retained assignment",
        operation_key=uuid.uuid4(),
    )
    other_homework = Homework(
        teacher_id=teacher.id,
        student_id=other_student.id,
        text="Other student's assignment",
        operation_key=uuid.uuid4(),
    )
    db_session.add_all([homework, other_homework, past_cancel, future_cancel])
    await db_session.flush()

    committed_path = homework_storage / uuid.uuid4().hex
    committed_path.write_bytes(b"retained")
    committed_attachment = HomeworkAttachment(
        owner_user_id=teacher.id,
        homework_id=homework.id,
        storage_key=committed_path.name,
        file_name="material.pdf",
        byte_size=8,
        staged_until=None,
    )
    staged_path = homework_storage / uuid.uuid4().hex
    staged_path.write_bytes(b"staged")
    staged_attachment = HomeworkAttachment(
        owner_user_id=student.id,
        storage_key=staged_path.name,
        file_name="staged.pdf",
        byte_size=6,
        staged_until=now + timedelta(days=1),
    )
    db_session.add_all([committed_attachment, staged_attachment])
    db_session.add(
        StudentAction(
            relationship_id=relationship.id,
            teacher_id=teacher.id,
            student_id=student.id,
            actor_id=student.id,
            action_type=StudentActionType.ACCOUNT_PROFILE_UPDATED.value,
            occurred_at=now - timedelta(days=1),
        )
    )
    student_assignment_intent = _intent(
        student.id,
        NotificationType.ASSIGNMENT_CREATED,
        "removed-student-assignment",
        now,
        source_id=homework.id,
    )
    sent_student_intent = _intent(
        student.id,
        NotificationType.DAILY_DIGEST,
        "already-sent-student-digest",
        now,
        state=NotificationState.SENT,
    )
    teacher_submission_intent = _intent(
        teacher.id,
        NotificationType.SUBMISSION_RECEIVED,
        "removed-student-submission",
        now,
        source_id=homework.id,
    )
    teacher_lesson_intent = _intent(
        teacher.id,
        NotificationType.LESSON_REMINDER,
        "removed-student-lesson",
        now,
        occurrence_source_type="event",
        occurrence_source_id=future_lesson.id,
    )
    unrelated_intent = _intent(
        teacher.id,
        NotificationType.SUBMISSION_RECEIVED,
        "other-student-submission",
        now,
        source_id=other_homework.id,
    )
    db_session.add_all(
        [
            student_assignment_intent,
            sent_student_intent,
            teacher_submission_intent,
            teacher_lesson_intent,
            unrelated_intent,
        ]
    )
    await db_session.commit()
    past_lesson_id = past_lesson.id
    future_lesson_id = future_lesson.id
    unrelated_lesson_id = unrelated_lesson.id
    recurring_id = recurring.id
    future_series_id = future_series.id
    past_cancel_id = past_cancel.id
    future_cancel_id = future_cancel.id
    homework_id = homework.id
    committed_attachment_id = committed_attachment.id
    staged_attachment_id = staged_attachment.id
    student_assignment_intent_id = student_assignment_intent.id
    sent_student_intent_id = sent_student_intent.id
    teacher_submission_intent_id = teacher_submission_intent.id
    teacher_lesson_intent_id = teacher_lesson_intent.id
    unrelated_intent_id = unrelated_intent.id
    teacher_headers = _auth(teacher)
    student_headers = _auth(student)

    preview_response = await client.post(
        f"/api/v1/auth/teacher/students/{student.id}/removal/preview",
        headers=teacher_headers,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["impact"] == {
        "one_off_lessons_to_delete": 1,
        "recurring_series_to_truncate": 1,
        "recurring_series_to_delete": 1,
        "future_cancellations_to_delete": 1,
        "staged_uploads_to_revoke": 1,
        "notifications_to_suppress": 3,
    }

    operation_key = uuid.uuid4()
    confirm_url = f"/api/v1/auth/teacher/students/{student.id}/removal/confirm"
    payload = {
        "preview_token": preview["confirmation_token"],
        "operation_key": str(operation_key),
    }
    confirmed = await client.post(confirm_url, json=payload, headers=teacher_headers)
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()
    assert result["relationship_id"] == str(relationship.id)
    assert result["operation_key"] == str(operation_key)
    assert result["cleanup_complete"] is True

    db_session.expire_all()
    await db_session.refresh(student)
    await db_session.refresh(relationship)
    assert student.teacher_id is None
    assert student.is_active is True
    assert relationship.removed_at is not None
    assert await db_session.get(Event, past_lesson_id) is not None
    assert await db_session.get(Event, future_lesson_id) is None
    assert await db_session.get(Event, unrelated_lesson_id) is not None
    retained_series = await db_session.get(RecurrentEvent, recurring_id)
    assert retained_series is not None
    assert retained_series.interval_end == to_storage_utc(
        now - timedelta(days=7) + timedelta(microseconds=1)
    )
    assert await db_session.get(RecurrentEvent, future_series_id) is None
    assert await db_session.get(RecurrentCancels, past_cancel_id) is not None
    assert await db_session.get(RecurrentCancels, future_cancel_id) is None
    assert await db_session.get(Homework, homework_id) is not None
    assert await db_session.get(HomeworkAttachment, committed_attachment_id) is not None
    assert committed_path.read_bytes() == b"retained"
    assert await db_session.get(HomeworkAttachment, staged_attachment_id) is None
    assert not staged_path.exists()
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(StudentAction)
            .where(StudentAction.relationship_id == relationship.id)
        )
        == 1
    )
    assert (
        await db_session.get(NotificationIntent, student_assignment_intent_id)
    ).state == NotificationState.SUPPRESSED.value
    assert (
        await db_session.get(NotificationIntent, teacher_submission_intent_id)
    ).state == NotificationState.SUPPRESSED.value
    assert (
        await db_session.get(NotificationIntent, teacher_lesson_intent_id)
    ).state == NotificationState.SUPPRESSED.value
    assert (
        await db_session.get(NotificationIntent, sent_student_intent_id)
    ).state == NotificationState.SENT.value
    assert (
        await db_session.get(NotificationIntent, unrelated_intent_id)
    ).state == NotificationState.PENDING.value

    former_homework = await client.get(
        f"/api/v1/homework/assignments/{homework_id}",
        headers=teacher_headers,
    )
    student_homework = await client.get(
        f"/api/v1/homework/assignments/{homework_id}",
        headers=student_headers,
    )
    retained_download = await client.get(
        f"/api/v1/homework/attachments/{committed_attachment_id}",
        headers=student_headers,
    )
    assert former_homework.status_code == student_homework.status_code == 200
    assert retained_download.status_code == 200
    assert retained_download.content == b"retained"
    submission_attempt = await client.post(
        f"/api/v1/homework/assignments/{homework_id}/submissions",
        json={"text": "No longer authorized", "operation_key": str(uuid.uuid4())},
        headers=student_headers,
    )
    assert submission_attempt.status_code == 404

    replay = await client.post(confirm_url, json=payload, headers=teacher_headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == result
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(StudentRemovalOperation)
            .where(StudentRemovalOperation.operation_key == operation_key)
        )
        == 1
    )

    new_teacher = User(role="TEACHER", timezone="UTC", full_name="New teacher")
    db_session.add(new_teacher)
    await db_session.flush()
    db_session.add(TeacherProfile(teacher_id=new_teacher.id, code="REJOIN01"))
    await db_session.commit()
    rejoined = await client.post(
        "/api/v1/auth/me/teacher",
        json={"teacher_code": "REJOIN01"},
        headers=student_headers,
    )
    assert rejoined.status_code == 200, rejoined.text
    replay_after_rejoin = await client.post(
        confirm_url, json=payload, headers=teacher_headers
    )
    assert replay_after_rejoin.status_code == 200
    await db_session.refresh(student)
    assert student.teacher_id == new_teacher.id


async def test_stale_removal_preview_is_rejected_without_mutation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    teacher, student, relationship = await _linked_users(db_session, now=now)
    await db_session.commit()

    preview = await client.post(
        f"/api/v1/auth/teacher/students/{student.id}/removal/preview",
        headers=_auth(teacher),
    )
    assert preview.status_code == 200, preview.text
    created_lesson = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(now + timedelta(days=2)),
        end=to_storage_utc(now + timedelta(days=2, hours=1)),
    )
    db_session.add(created_lesson)
    await db_session.commit()

    confirmation = await client.post(
        f"/api/v1/auth/teacher/students/{student.id}/removal/confirm",
        headers=_auth(teacher),
        json={
            "preview_token": preview.json()["confirmation_token"],
            "operation_key": str(uuid.uuid4()),
        },
    )
    assert confirmation.status_code == 409
    await db_session.refresh(student)
    await db_session.refresh(relationship)
    assert student.teacher_id == teacher.id
    assert relationship.removed_at is None
    assert await db_session.get(Event, created_lesson.id) is not None


async def test_removal_racing_submission_serializes_student_write(
    removal_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    async with removal_session_factory() as session:
        teacher, student, relationship = await _linked_users(session, now=now)
        homework = Homework(
            teacher_id=teacher.id,
            student_id=student.id,
            text="Submit an answer",
            operation_key=uuid.uuid4(),
        )
        session.add(homework)
        await session.flush()
        teacher_id, student_id, homework_id = teacher.id, student.id, homework.id
        preview = await preview_student_removal(session, teacher, student_id)
        await session.commit()
    request = StudentRemovalConfirm(
        preview_token=preview.confirmation_token,
        operation_key=uuid.uuid4(),
    )

    async def remove_student() -> bool:
        try:
            async with removal_session_factory() as session:
                teacher = await session.get(User, teacher_id)
                assert teacher is not None
                await confirm_student_removal(
                    session,
                    teacher,
                    student_id,
                    request,
                    HomeworkConfig(STORAGE_PATH=tmp_path),
                )
                return True
        except Conflict:
            return False

    async def submit_homework() -> bool:
        try:
            async with removal_session_factory() as session:
                student = await session.get(User, student_id)
                assert student is not None
                await create_submission(
                    session,
                    student,
                    homework_id,
                    HomeworkSubmissionCreate(
                        text="My answer", operation_key=uuid.uuid4()
                    ),
                )
                return True
        except Conflict, Forbidden, NotFound:
            return False
        except HTTPException as exc:
            if exc.status_code == 503:
                return False
            raise

    removal_succeeded, submission_succeeded = await asyncio.gather(
        remove_student(), submit_homework()
    )
    async with removal_session_factory() as session:
        student = await session.get(User, student_id)
        relationship = await session.scalar(
            select(StudentTeacherRelationship).where(
                StudentTeacherRelationship.student_id == student_id,
                StudentTeacherRelationship.teacher_id == teacher_id,
            )
        )
        submission_count = await session.scalar(
            select(func.count())
            .select_from(HomeworkSubmission)
            .where(HomeworkSubmission.homework_id == homework_id)
        )
        assert student is not None and relationship is not None
        assert submission_count == int(submission_succeeded)
        if student.teacher_id is None:
            assert relationship.removed_at is not None
            if submission_count:
                action_at = await session.scalar(
                    select(StudentAction.occurred_at).where(
                        StudentAction.resource_type == "homework_submission",
                        StudentAction.action_type
                        == StudentActionType.HOMEWORK_SUBMISSION_CREATED.value,
                    )
                )
                assert action_at is not None
                assert action_at <= relationship.removed_at
        else:
            assert student.teacher_id == teacher_id
            assert relationship.removed_at is None
            assert submission_succeeded or not removal_succeeded


async def test_removal_racing_teacher_booking_revalidates_under_schedule_guard(
    removal_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    async with removal_session_factory() as session:
        teacher, student, relationship = await _linked_users(session, now=now)
        await session.commit()
        teacher_id, student_id = teacher.id, student.id
        preview = await preview_student_removal(session, teacher, student_id)
        await session.commit()
    booking_date = now.date() + timedelta(days=1)
    while booking_date.weekday() >= 5:
        booking_date += timedelta(days=1)
    start = datetime.combine(booking_date, time(10), UTC)
    if start <= now:
        start += timedelta(days=7)

    request = StudentRemovalConfirm(
        preview_token=preview.confirmation_token,
        operation_key=uuid.uuid4(),
    )

    async def remove_student() -> bool:
        try:
            async with removal_session_factory() as session:
                teacher = await session.get(User, teacher_id)
                assert teacher is not None
                await confirm_student_removal(
                    session,
                    teacher,
                    student_id,
                    request,
                    HomeworkConfig(STORAGE_PATH=tmp_path),
                )
                return True
        except Conflict:
            return False

    async def book_lesson() -> bool:
        try:
            async with removal_session_factory() as session:
                teacher = await session.get(User, teacher_id)
                assert teacher is not None
                await create_event(
                    session,
                    EventCreate(
                        user_id=student_id,
                        event_type=EventType.LESSON,
                        start=start,
                        end=start + timedelta(hours=1),
                    ),
                    EventValidator(expected_event_type=EventType.LESSON),
                    teacher_id,
                    actor_is_teacher=True,
                    actor=teacher,
                )
                return True
        except Conflict, Forbidden:
            return False

    removal_succeeded, booking_succeeded = await asyncio.gather(
        remove_student(), book_lesson()
    )
    async with removal_session_factory() as session:
        student = await session.get(User, student_id)
        relationship = await session.scalar(
            select(StudentTeacherRelationship).where(
                StudentTeacherRelationship.student_id == student_id,
                StudentTeacherRelationship.teacher_id == teacher_id,
            )
        )
        lesson_count = await session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.teacher_id == teacher_id,
                Event.user_id == student_id,
                Event.event_type == EventType.LESSON.value,
            )
        )
        assert student is not None and relationship is not None
        assert lesson_count == int(booking_succeeded)
        if student.teacher_id is None:
            assert relationship.removed_at is not None
            assert not booking_succeeded
        else:
            assert student.teacher_id == teacher_id
            assert relationship.removed_at is None
            assert not removal_succeeded or not booking_succeeded


async def test_removal_is_idempotent_on_each_supported_database(
    removal_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    teacher = User(role="TEACHER", timezone="UTC")
    student = User(role="STUDENT", timezone="UTC")
    async with removal_session_factory() as session:
        session.add(teacher)
        await session.flush()
        student.teacher_id = teacher.id
        session.add(student)
        await session.flush()
        relationship = StudentTeacherRelationship(
            teacher_id=teacher.id,
            student_id=student.id,
            linked_at=now - timedelta(days=1),
        )
        future_lesson = Event(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type=EventType.LESSON.value,
            start=to_storage_utc(now + timedelta(days=1)),
            end=to_storage_utc(now + timedelta(days=1, hours=1)),
        )
        session.add_all([relationship, future_lesson])
        await session.commit()
        teacher_id = teacher.id
        student_id = student.id
        relationship_id = relationship.id
        future_lesson_id = future_lesson.id

    async with removal_session_factory() as session:
        teacher = await session.get(User, teacher_id)
        assert teacher is not None
        preview = await preview_student_removal(session, teacher, student_id)
        request = StudentRemovalConfirm(
            preview_token=preview.confirmation_token,
            operation_key=uuid.uuid4(),
        )
    config = HomeworkConfig(STORAGE_PATH=tmp_path)
    async with removal_session_factory() as session:
        teacher = await session.get(User, teacher_id)
        assert teacher is not None
        first_result = await confirm_student_removal(
            session, teacher, student_id, request, config
        )
    async with removal_session_factory() as session:
        teacher = await session.get(User, teacher_id)
        assert teacher is not None
        replay_result = await confirm_student_removal(
            session, teacher, student_id, request, config
        )
        student = await session.get(User, student_id)
        relationship = await session.get(StudentTeacherRelationship, relationship_id)
        operation_count = await session.scalar(
            select(func.count())
            .select_from(StudentRemovalOperation)
            .where(StudentRemovalOperation.operation_key == request.operation_key)
        )
        assert student is not None and student.teacher_id is None
        assert relationship is not None and relationship.removed_at is not None
        assert await session.get(Event, future_lesson_id) is None
        assert operation_count == 1
    assert replay_result == first_result


async def test_rejoin_racing_removal_never_restores_the_removed_link(
    removal_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    old_teacher = User(role="TEACHER", timezone="UTC")
    new_teacher = User(role="TEACHER", timezone="UTC")
    student = User(role="STUDENT", timezone="UTC")
    async with removal_session_factory() as session:
        session.add_all([old_teacher, new_teacher])
        await session.flush()
        student.teacher_id = old_teacher.id
        session.add(student)
        await session.flush()
        relationship = StudentTeacherRelationship(
            teacher_id=old_teacher.id,
            student_id=student.id,
            linked_at=now - timedelta(days=1),
        )
        session.add_all(
            [
                relationship,
                TeacherProfile(teacher_id=new_teacher.id, code="RACEJOIN"),
            ]
        )
        await session.commit()
        old_teacher_id = old_teacher.id
        new_teacher_id = new_teacher.id
        student_id = student.id
        relationship_id = relationship.id

    async with removal_session_factory() as session:
        teacher = await session.get(User, old_teacher_id)
        assert teacher is not None
        preview = await preview_student_removal(session, teacher, student_id)
        request = StudentRemovalConfirm(
            preview_token=preview.confirmation_token,
            operation_key=uuid.uuid4(),
        )

    async def remove() -> None:
        async with removal_session_factory() as session:
            teacher = await session.get(User, old_teacher_id)
            assert teacher is not None
            await confirm_student_removal(
                session,
                teacher,
                student_id,
                request,
                HomeworkConfig(STORAGE_PATH=tmp_path),
            )

    async def rejoin() -> str:
        async with removal_session_factory() as session:
            current_student = await session.get(User, student_id)
            assert current_student is not None
            try:
                await auth_service.link_student_to_teacher(
                    session, current_student, "RACEJOIN"
                )
            except StudentAlreadyLinked:
                return "linked-before-removal"
            return "rejoined-after-removal"

    removal_result, rejoin_result = await asyncio.gather(
        remove(), rejoin(), return_exceptions=True
    )
    assert not isinstance(removal_result, BaseException), removal_result
    assert rejoin_result in {"linked-before-removal", "rejoined-after-removal"}

    async with removal_session_factory() as session:
        current_student = await session.get(User, student_id)
        old_relationship = await session.get(
            StudentTeacherRelationship, relationship_id
        )
        active_relationship_count = await session.scalar(
            select(func.count())
            .select_from(StudentTeacherRelationship)
            .where(
                StudentTeacherRelationship.student_id == student_id,
                StudentTeacherRelationship.removed_at.is_(None),
            )
        )
        assert current_student is not None
        assert current_student.teacher_id in {None, new_teacher_id}
        assert old_relationship is not None and old_relationship.removed_at is not None
        assert active_relationship_count == int(current_student.teacher_id is not None)

        old_teacher = await session.get(User, old_teacher_id)
        assert old_teacher is not None
        replay = await confirm_student_removal(
            session,
            old_teacher,
            student_id,
            request,
            HomeworkConfig(STORAGE_PATH=tmp_path),
        )
        await session.refresh(current_student)
        assert current_student.teacher_id in {None, new_teacher_id}
        assert replay.operation_key == request.operation_key


async def test_removal_preserves_approved_effect_matrix_on_each_database(
    removal_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    teacher = User(role="TEACHER", timezone="UTC", full_name="Original teacher")
    student = User(role="STUDENT", timezone="UTC", full_name="Detached student")
    other_student = User(role="STUDENT", timezone="UTC", full_name="Other student")
    committed_key = uuid.uuid4().hex
    staged_key = uuid.uuid4().hex
    committed_path = tmp_path / committed_key
    staged_path = tmp_path / staged_key
    committed_path.write_bytes(b"keep")
    staged_path.write_bytes(b"revoke")

    async with removal_session_factory() as session:
        session.add_all([teacher, student, other_student])
        await session.flush()
        student.teacher_id = teacher.id
        other_student.teacher_id = teacher.id
        relationship = StudentTeacherRelationship(
            teacher_id=teacher.id,
            student_id=student.id,
            linked_at=now - timedelta(days=30),
        )
        other_relationship = StudentTeacherRelationship(
            teacher_id=teacher.id,
            student_id=other_student.id,
            linked_at=now - timedelta(days=10),
        )
        past_lesson = Event(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type=EventType.LESSON.value,
            start=to_storage_utc(now - timedelta(days=2)),
            end=to_storage_utc(now - timedelta(days=2) + timedelta(hours=1)),
        )
        ongoing_lesson = Event(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type=EventType.LESSON.value,
            start=to_storage_utc(now - timedelta(minutes=30)),
            end=to_storage_utc(now + timedelta(minutes=30)),
        )
        future_lesson = Event(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type=EventType.LESSON.value,
            start=to_storage_utc(now + timedelta(days=1)),
            end=to_storage_utc(now + timedelta(days=1, hours=1)),
        )
        unrelated_lesson = Event(
            teacher_id=teacher.id,
            user_id=other_student.id,
            event_type=EventType.LESSON.value,
            start=to_storage_utc(now + timedelta(days=1)),
            end=to_storage_utc(now + timedelta(days=1, hours=1)),
        )
        recurring = RecurrentEvent(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type=EventType.LESSON.value,
            start=to_storage_utc(now - timedelta(days=14)),
            end=to_storage_utc(now - timedelta(days=14) + timedelta(hours=1)),
            interval=7,
            interval_end=to_storage_utc(now + timedelta(days=14)),
            recurrence_mode=RecurrenceMode.UTC_STEP.value,
            recurrence_timezone=None,
        )
        future_series = RecurrentEvent(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type=EventType.LESSON.value,
            start=to_storage_utc(now + timedelta(days=7)),
            end=to_storage_utc(now + timedelta(days=7, hours=1)),
            interval=7,
            interval_end=to_storage_utc(now + timedelta(days=28)),
            recurrence_mode=RecurrenceMode.UTC_STEP.value,
            recurrence_timezone=None,
        )
        retained_homework = Homework(
            teacher_id=teacher.id,
            student_id=student.id,
            text="Retained completed task",
            status="COMPLETE",
            completion_source="TEACHER",
            completed_at=now - timedelta(days=1),
            operation_key=uuid.uuid4(),
        )
        unrelated_homework = Homework(
            teacher_id=teacher.id,
            student_id=other_student.id,
            text="Unrelated task",
            operation_key=uuid.uuid4(),
        )
        session.add_all(
            [
                relationship,
                other_relationship,
                past_lesson,
                ongoing_lesson,
                future_lesson,
                unrelated_lesson,
                recurring,
                future_series,
                retained_homework,
                unrelated_homework,
            ]
        )
        await session.flush()
        past_cancel = RecurrentCancels(
            recurrent_event_id=recurring.id,
            start=to_storage_utc(now - timedelta(days=14)),
            end=to_storage_utc(now - timedelta(days=14) + timedelta(hours=1)),
        )
        future_cancel = RecurrentCancels(
            recurrent_event_id=recurring.id,
            start=to_storage_utc(now + timedelta(days=7)),
            end=to_storage_utc(now + timedelta(days=7, hours=1)),
        )
        submission = HomeworkSubmission(
            homework_id=retained_homework.id,
            student_id=student.id,
            version=1,
            text="Retained solution",
            operation_key=uuid.uuid4(),
        )
        committed_attachment = HomeworkAttachment(
            owner_user_id=teacher.id,
            homework_id=retained_homework.id,
            storage_key=committed_key,
            file_name="committed.pdf",
            byte_size=4,
            staged_until=None,
        )
        staged_attachment = HomeworkAttachment(
            owner_user_id=student.id,
            storage_key=staged_key,
            file_name="staged.pdf",
            byte_size=6,
            staged_until=now + timedelta(days=1),
        )
        session.add_all(
            [
                past_cancel,
                future_cancel,
                submission,
                committed_attachment,
                staged_attachment,
                StudentAction(
                    relationship_id=relationship.id,
                    teacher_id=teacher.id,
                    student_id=student.id,
                    actor_id=student.id,
                    action_type=StudentActionType.ACCOUNT_PROFILE_UPDATED.value,
                    occurred_at=now - timedelta(days=1),
                ),
            ]
        )
        student_assignment_intent = _intent(
            student.id,
            NotificationType.ASSIGNMENT_CREATED,
            f"student-assignment-{uuid.uuid4()}",
            now,
            source_id=retained_homework.id,
        )
        teacher_submission_intent = _intent(
            teacher.id,
            NotificationType.SUBMISSION_RECEIVED,
            f"teacher-submission-{uuid.uuid4()}",
            now,
            source_id=retained_homework.id,
        )
        teacher_lesson_intent = _intent(
            teacher.id,
            NotificationType.LESSON_REMINDER,
            f"teacher-lesson-{uuid.uuid4()}",
            now,
            occurrence_source_type="event",
            occurrence_source_id=future_lesson.id,
        )
        sent_intent = _intent(
            student.id,
            NotificationType.DAILY_DIGEST,
            f"sent-digest-{uuid.uuid4()}",
            now,
            state=NotificationState.SENT,
        )
        unrelated_intent = _intent(
            teacher.id,
            NotificationType.SUBMISSION_RECEIVED,
            f"unrelated-submission-{uuid.uuid4()}",
            now,
            source_id=unrelated_homework.id,
        )
        session.add_all(
            [
                student_assignment_intent,
                teacher_submission_intent,
                teacher_lesson_intent,
                sent_intent,
                unrelated_intent,
            ]
        )
        await session.commit()
        ids = {
            "teacher": teacher.id,
            "student": student.id,
            "relationship": relationship.id,
            "past_lesson": past_lesson.id,
            "ongoing_lesson": ongoing_lesson.id,
            "future_lesson": future_lesson.id,
            "unrelated_lesson": unrelated_lesson.id,
            "recurring": recurring.id,
            "future_series": future_series.id,
            "past_cancel": past_cancel.id,
            "future_cancel": future_cancel.id,
            "homework": retained_homework.id,
            "submission": submission.id,
            "committed_attachment": committed_attachment.id,
            "staged_attachment": staged_attachment.id,
            "student_intent": student_assignment_intent.id,
            "teacher_submission_intent": teacher_submission_intent.id,
            "teacher_lesson_intent": teacher_lesson_intent.id,
            "sent_intent": sent_intent.id,
            "unrelated_intent": unrelated_intent.id,
        }

    async with removal_session_factory() as session:
        teacher = await session.get(User, ids["teacher"])
        assert teacher is not None
        preview = await preview_student_removal(session, teacher, ids["student"])
    assert preview.impact.one_off_lessons_to_delete == 2
    assert preview.impact.recurring_series_to_truncate == 1
    assert preview.impact.recurring_series_to_delete == 1
    assert preview.impact.future_cancellations_to_delete == 1
    assert preview.impact.staged_uploads_to_revoke == 1
    assert preview.impact.notifications_to_suppress == 3

    request = StudentRemovalConfirm(
        preview_token=preview.confirmation_token,
        operation_key=uuid.uuid4(),
    )
    async with removal_session_factory() as session:
        teacher = await session.get(User, ids["teacher"])
        assert teacher is not None
        result = await confirm_student_removal(
            session,
            teacher,
            ids["student"],
            request,
            HomeworkConfig(STORAGE_PATH=tmp_path),
        )
    assert result.cleanup_complete

    async with removal_session_factory() as session:
        student = await session.get(User, ids["student"])
        relationship = await session.get(
            StudentTeacherRelationship, ids["relationship"]
        )
        assert student is not None and student.is_active and student.teacher_id is None
        assert relationship is not None and relationship.removed_at is not None
        assert await session.get(Event, ids["past_lesson"]) is not None
        assert await session.get(Event, ids["ongoing_lesson"]) is None
        assert await session.get(Event, ids["future_lesson"]) is None
        assert await session.get(Event, ids["unrelated_lesson"]) is not None
        preserved_series = await session.get(RecurrentEvent, ids["recurring"])
        assert preserved_series is not None
        assert preserved_series.interval_end == to_storage_utc(
            now - timedelta(days=7) + timedelta(microseconds=1)
        )
        assert await session.get(RecurrentEvent, ids["future_series"]) is None
        assert await session.get(RecurrentCancels, ids["past_cancel"]) is not None
        assert await session.get(RecurrentCancels, ids["future_cancel"]) is None
        homework = await session.get(Homework, ids["homework"])
        assert homework is not None
        assert homework.status == "COMPLETE"
        assert homework.completion_source == "TEACHER"
        assert await session.get(HomeworkSubmission, ids["submission"]) is not None
        assert (
            await session.get(HomeworkAttachment, ids["committed_attachment"])
            is not None
        )
        assert await session.get(HomeworkAttachment, ids["staged_attachment"]) is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(StudentAction)
                .where(
                    StudentAction.relationship_id == ids["relationship"],
                    StudentAction.action_type
                    == StudentActionType.ACCOUNT_PROFILE_UPDATED.value,
                )
            )
            == 1
        )
        expected_states = {
            "student_intent": NotificationState.SUPPRESSED.value,
            "teacher_submission_intent": NotificationState.SUPPRESSED.value,
            "teacher_lesson_intent": NotificationState.SUPPRESSED.value,
            "sent_intent": NotificationState.SENT.value,
            "unrelated_intent": NotificationState.PENDING.value,
        }
        for key, state in expected_states.items():
            intent = await session.get(NotificationIntent, ids[key])
            assert intent is not None and intent.state == state
    assert committed_path.read_bytes() == b"keep"
    assert not staged_path.exists()
