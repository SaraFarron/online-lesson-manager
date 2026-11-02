from collections import Counter
from collections.abc import Generator
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.auth.utils import create_access_token
from backend.constants import EventType
from backend.events.models import Event
from backend.events.time_utils import normalize_utc, to_storage_utc
from backend.homework.models import Homework
from backend.main import app
from backend.notifications.config import (
    NotificationConfig,
    get_notification_settings,
)
from backend.notifications.constants import NotificationState, NotificationType
from backend.notifications.homework_producers import produce_homework_reminders
from backend.notifications.models import NotificationIntent
from backend.notifications.router import get_notification_clock
from backend.notifications.schedule_producers import produce_schedule_notifications
from telegram.api.notifications import NotificationClient
from telegram.notifications.messages import render_notification
from telegram.notifications.worker import NotificationWorker
from tests.bot_support import RecordingSession

SERVICE_KEY = "notification-service-integration-test-key-32"


@pytest.fixture
def notification_api_key() -> Generator[str]:
    previous_settings = app.dependency_overrides.get(get_notification_settings)
    app.dependency_overrides[get_notification_settings] = lambda: NotificationConfig(
        SERVICE_CREDENTIAL=SecretStr(SERVICE_KEY)
    )
    try:
        yield SERVICE_KEY
    finally:
        if previous_settings is None:
            app.dependency_overrides.pop(get_notification_settings, None)
        else:
            app.dependency_overrides[get_notification_settings] = previous_settings


def _authorization(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


def _run_at() -> datetime:
    timestamp = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(
        minutes=10
    )
    if timestamp.hour == 23 and timestamp.minute >= 45:
        timestamp = (timestamp + timedelta(days=1)).replace(
            hour=0, minute=30, second=0, microsecond=0
        )
    if timestamp.hour == 0 and timestamp.minute == 0:
        timestamp += timedelta(minutes=1)
    return timestamp


def _event(
    teacher: User,
    student: User,
    starts_at: datetime,
    *,
    duration_minutes: int = 45,
) -> Event:
    return Event(
        id=uuid4(),
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(starts_at),
        end=to_storage_utc(starts_at + timedelta(minutes=duration_minutes)),
    )


@pytest.mark.asyncio
async def test_preferences_to_recorded_delivery_for_every_notification_type(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    run_at = _run_at()
    previous_clock = app.dependency_overrides.get(get_notification_clock)
    app.dependency_overrides[get_notification_clock] = lambda: run_at
    try:
        digest_minute = run_at.hour * 60 + run_at.minute - 1
        history_time = run_at - timedelta(days=3)
        teacher_chat_id = 500000001
        student_chat_id = 500000002
        teacher = User(
            id=uuid4(),
            email=f"{uuid4()}@example.com",
            full_name="Tutor <& Co",
            role="TEACHER",
            timezone="UTC",
            telegram_private_chat_id=teacher_chat_id,
            notification_lessons_today=digest_minute,
            notification_schedule_updated_at=history_time,
        )
        student = User(
            id=uuid4(),
            email=f"{uuid4()}@example.com",
            full_name="Student Example",
            role="STUDENT",
            timezone="UTC",
            teacher_id=teacher.id,
            telegram_private_chat_id=student_chat_id,
            notification_lessons_today=digest_minute,
            notification_schedule_updated_at=history_time,
        )
        student_profile = StudentProfile(
            student_id=student.id,
            notification_lesson=15,
            notification_lesson_updated_at=history_time,
            notification_homework=2,
            notification_homework_updated_at=history_time,
            notification_homework_assignment=True,
        )
        teacher_profile = TeacherProfile(
            teacher_id=teacher.id,
            code=f"t_{uuid4().hex[:6].upper()}",
            notification_homework_submission=True,
        )
        today_start = run_at + timedelta(minutes=15)
        due_start = datetime.combine(
            run_at.date() + timedelta(days=2), time(10, 0), tzinfo=UTC
        )
        today_lesson = _event(teacher, student, today_start)
        due_lesson = _event(teacher, student, due_start)
        db_session.add_all(
            [
                teacher,
                student,
                student_profile,
                teacher_profile,
                today_lesson,
                due_lesson,
            ]
        )
        await db_session.commit()

        teacher_headers = _authorization(teacher)
        student_headers = _authorization(student)

        async def create_assignment(text: str, lesson: Event | None = None) -> UUID:
            payload: dict[str, object] = {
                "student_id": str(student.id),
                "text": text,
            }
            if lesson is not None:
                payload["lesson_occurrence"] = {
                    "kind": "EVENT",
                    "source_id": str(lesson.id),
                    "starts_at": normalize_utc(lesson.start).isoformat(),
                }
            response = await client.post(
                "/api/v1/homework/assignments",
                headers={
                    **teacher_headers,
                    "Idempotency-Key": str(uuid4()),
                },
                json=payload,
            )
            assert response.status_code == 201, response.text
            return UUID(response.json()["id"])

        today_homework_id = await create_assignment(
            "Review today's notes", today_lesson
        )
        due_homework_id = await create_assignment("Prepare pages 4–6", due_lesson)
        submitted_homework_id = await create_assignment("Solve exercise 7")
        submission_response = await client.post(
            f"/api/v1/homework/assignments/{submitted_homework_id}/submissions",
            headers=student_headers,
            json={
                "text": "My worked solution",
                "operation_key": str(uuid4()),
            },
        )
        assert submission_response.status_code == 201, submission_response.text
        assert submission_response.json()["version"] == 1

        await produce_schedule_notifications(db_session, now=run_at)
        await produce_homework_reminders(db_session, now=run_at)
        await produce_schedule_notifications(db_session, now=run_at)
        await produce_homework_reminders(db_session, now=run_at)
        await db_session.commit()

        intents = list(
            (
                await db_session.scalars(
                    select(NotificationIntent).order_by(NotificationIntent.id)
                )
            ).all()
        )
        intents_by_id = {intent.id: intent for intent in intents}
        assert Counter(intent.notification_type for intent in intents) == {
            NotificationType.DAILY_DIGEST.value: 2,
            NotificationType.LESSON_REMINDER.value: 1,
            NotificationType.HOMEWORK_REMINDER.value: 1,
            NotificationType.ASSIGNMENT_CREATED.value: 3,
            NotificationType.SUBMISSION_RECEIVED.value: 1,
        }
        assert len({intent.dedupe_key for intent in intents}) == len(intents)
        due_homework = await db_session.get(Homework, due_homework_id)
        assert due_homework is not None
        assert due_homework.status == "INCOMPLETE"
        assert {today_homework_id, due_homework_id, submitted_homework_id} == {
            homework.id
            for homework in (await db_session.scalars(select(Homework))).all()
        }

        recording_session = RecordingSession()
        bot = Bot(
            "123456789:test-token-for-local-recording",
            session=recording_session,
        )
        transport = NotificationClient(client, notification_api_key)
        worker = NotificationWorker(bot, transport)
        delivered = []
        while True:
            batch = await transport.claim(limit=1)
            if not batch.items:
                break
            item = batch.items[0]
            delivered.append(item)
            await worker._deliver(item)

        assert Counter(item.notification_type for item in delivered) == Counter(
            {
                NotificationType.DAILY_DIGEST.value: 2,
                NotificationType.LESSON_REMINDER.value: 1,
                NotificationType.HOMEWORK_REMINDER.value: 1,
                NotificationType.ASSIGNMENT_CREATED.value: 3,
                NotificationType.SUBMISSION_RECEIVED.value: 1,
            }
        )
        assert all(
            item.scheduled_at == normalize_utc(intents_by_id[item.id].scheduled_at)
            for item in delivered
        )
        by_type = {
            kind: [item for item in delivered if item.notification_type == kind]
            for kind in NotificationType
        }
        date_text = run_at.date().isoformat()
        today_text = today_start.strftime("%H:%M")
        today_end_text = (today_start + timedelta(minutes=45)).strftime("%H:%M")
        today_occurrence = f"{today_text}–{today_end_text}"
        assert sorted(
            item.chat_id for item in by_type[NotificationType.DAILY_DIGEST]
        ) == [
            teacher_chat_id,
            student_chat_id,
        ]
        teacher_digest = next(
            item
            for item in by_type[NotificationType.DAILY_DIGEST]
            if item.recipient_id == teacher.id
        )
        student_digest = next(
            item
            for item in by_type[NotificationType.DAILY_DIGEST]
            if item.recipient_id == student.id
        )
        assert teacher_digest.content == [
            f"Schedule for {date_text}",
            f"{today_occurrence} Lesson — Student Example",
            "Open /start in Telegram to view the related details.",
        ]
        assert student_digest.content == [
            f"Schedule for {date_text}",
            f"{today_occurrence} Lesson",
            f"Incomplete homework for {today_text} lesson: 1",
            "Open /start in Telegram to view the related details.",
        ]
        assert teacher_digest.scheduled_at == run_at.replace(
            minute=digest_minute % 60
        ).replace(hour=digest_minute // 60)
        lesson_reminder = by_type[NotificationType.LESSON_REMINDER][0]
        assert lesson_reminder.recipient_id == student.id
        assert lesson_reminder.chat_id == student_chat_id
        assert lesson_reminder.scheduled_at == run_at
        assert lesson_reminder.content == [
            f"Your lesson starts {today_start:%A, %Y-%m-%d at %H:%M} "
            f"and ends at {today_start + timedelta(minutes=45):%H:%M} (UTC).",
            "Open /start in Telegram to view the related details.",
        ]
        homework_reminder = by_type[NotificationType.HOMEWORK_REMINDER][0]
        assert homework_reminder.recipient_id == student.id
        assert homework_reminder.chat_id == student_chat_id
        assert homework_reminder.scheduled_at == run_at.replace(
            minute=digest_minute % 60
        ).replace(hour=digest_minute // 60)
        assert homework_reminder.content == [
            f"Lesson: {due_start:%A, %Y-%m-%d at %H:%M} (UTC)",
            "Text: Prepare pages 4–6",
            "Open /start in Telegram to view the related details.",
        ]
        assignments = by_type[NotificationType.ASSIGNMENT_CREATED]
        assert {item.recipient_id for item in assignments} == {student.id}
        assert {item.chat_id for item in assignments} == {student_chat_id}
        assert {line for item in assignments for line in item.content} == {
            "From: Tutor <& Co",
            "Text: Review today's notes",
            "Text: Prepare pages 4–6",
            "Text: Solve exercise 7",
            "Open /start in Telegram to view the related details.",
        }
        submission = by_type[NotificationType.SUBMISSION_RECEIVED][0]
        assert submission.recipient_id == teacher.id
        assert submission.chat_id == teacher_chat_id
        assert submission.content == [
            "From: Student Example",
            "Solution version: 1",
            "Assignment:",
            "Text: Solve exercise 7",
            "Student solution:",
            "Text: My worked solution",
            "Open /start in Telegram to view the related details.",
        ]

        expected_chats = [item.chat_id for item in delivered]
        sent_messages = [
            call for call in recording_session.calls if isinstance(call, SendMessage)
        ]
        assert [int(message.chat_id) for message in sent_messages] == expected_chats
        assert len(sent_messages) == len(delivered)
        assert [message.text for message in sent_messages] == [
            render_notification(item)[0] for item in delivered
        ]
        persisted = list(
            (
                await db_session.scalars(
                    select(NotificationIntent).order_by(NotificationIntent.id)
                )
            ).all()
        )
        for intent in persisted:
            await db_session.refresh(intent)
        assert all(intent.state == NotificationState.SENT for intent in persisted)
        assert all(intent.attempt_count == 1 for intent in persisted)
        assert all(intent.sent_at is not None for intent in persisted)
        await bot.session.close()
    finally:
        if previous_clock is None:
            app.dependency_overrides.pop(get_notification_clock, None)
        else:
            app.dependency_overrides[get_notification_clock] = previous_clock


@pytest.mark.asyncio
async def test_changed_student_linkage_suppresses_claim_before_telegram_send(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    teacher = User(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        role="TEACHER",
        timezone="UTC",
    )
    student = User(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
        telegram_private_chat_id=500000003,
    )
    db_session.add_all(
        [
            teacher,
            student,
            StudentProfile(
                student_id=student.id,
                notification_homework_assignment=True,
            ),
        ]
    )
    await db_session.commit()

    created = await client.post(
        "/api/v1/homework/assignments",
        headers={
            **_authorization(teacher),
            "Idempotency-Key": str(uuid4()),
        },
        json={"student_id": str(student.id), "text": "Read the notes"},
    )
    assert created.status_code == 201, created.text
    intent = await db_session.scalar(
        select(NotificationIntent).where(
            NotificationIntent.notification_type
            == NotificationType.ASSIGNMENT_CREATED.value
        )
    )
    assert intent is not None

    student.teacher_id = None
    await db_session.commit()
    transport = NotificationClient(client, notification_api_key)
    batch = await transport.claim(limit=1)

    assert batch.items == []
    await db_session.refresh(intent)
    assert intent.state == NotificationState.SUPPRESSED
    assert intent.last_error_code == "student_link_inactive"
