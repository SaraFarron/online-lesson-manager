import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.constants import EventType
from backend.events.models import Event
from backend.events.occurrences import OccurrenceSourceKind
from backend.homework.models import Homework, HomeworkSubmission
from backend.main import app
from backend.models import Base
from backend.notifications.config import (
    NotificationConfig,
    get_notification_settings,
)
from backend.notifications.constants import (
    AcknowledgementOutcome,
    NotificationState,
    NotificationType,
)
from backend.notifications.models import NotificationIntent
from backend.notifications.router import get_notification_clock
from backend.notifications.runner import run_producer_runner
from backend.notifications.service import claim_pending_intents

SERVICE_KEY = "notification-service-test-key-with-32-characters"


@pytest.fixture
def notification_api_key() -> Generator[str]:
    previous_settings = app.dependency_overrides.get(get_notification_settings)
    previous_clock = app.dependency_overrides.get(get_notification_clock)
    app.dependency_overrides[get_notification_settings] = lambda: NotificationConfig(
        SERVICE_CREDENTIAL=SERVICE_KEY
    )
    app.dependency_overrides[get_notification_clock] = lambda: NOW
    try:
        yield SERVICE_KEY
    finally:
        if previous_settings is None:
            app.dependency_overrides.pop(get_notification_settings, None)
        else:
            app.dependency_overrides[get_notification_settings] = previous_settings
        if previous_clock is None:
            app.dependency_overrides.pop(get_notification_clock, None)
        else:
            app.dependency_overrides[get_notification_clock] = previous_clock


NOW = datetime(2026, 11, 1, 10, tzinfo=UTC)
SERVICE_ROOT = "/api/v1/notifications/service"


async def _create_assignment_intent(
    session: AsyncSession,
    *,
    now: datetime = NOW,
    chat_id: int | None = 123456789,
    notification_enabled: bool = True,
    recipient_active: bool = True,
    assignment_completed: bool = False,
) -> tuple[User, NotificationIntent]:
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    teacher = User(
        id=teacher_id,
        email=f"teacher-{teacher_id}@example.com",
        role="TEACHER",
        timezone="UTC",
        is_active=True,
    )
    student = User(
        id=student_id,
        email=f"student-{student_id}@example.com",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher_id,
        is_active=recipient_active,
        telegram_private_chat_id=chat_id,
    )
    profile = StudentProfile(
        student_id=student_id,
        notification_homework=2,
        notification_homework_assignment=notification_enabled,
    )
    homework = Homework(
        id=uuid.uuid4(),
        teacher_id=teacher_id,
        student_id=student_id,
        text="Complete the exercise",
        status="COMPLETE" if assignment_completed else "INCOMPLETE",
        completion_source="TEACHER" if assignment_completed else None,
        completed_at=now if assignment_completed else None,
        operation_key=uuid.uuid4(),
    )
    intent = NotificationIntent(
        id=uuid.uuid4(),
        recipient_id=student_id,
        notification_type=NotificationType.ASSIGNMENT_CREATED,
        dedupe_key=f"assignment:{student_id}:{homework.id}",
        source_id=homework.id,
        scheduled_at=now,
        available_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=24),
    )
    session.add_all([teacher, student, profile, homework, intent])
    await session.flush()
    return student, intent


async def test_notification_service_credential_is_separate_and_required(
    client: AsyncClient, notification_api_key: str, bot_service_key: str
) -> None:
    endpoint = f"{SERVICE_ROOT}/health"
    assert (await client.get(endpoint)).status_code == 401
    assert (
        await client.get(endpoint, headers={"X-Bot-Service-Key": bot_service_key})
    ).status_code == 401
    assert (
        await client.get(
            endpoint,
            headers={"X-Notification-Service-Key": notification_api_key},
        )
    ).status_code == 200


async def test_notification_service_fails_closed_when_not_configured(
    client: AsyncClient,
) -> None:
    previous_settings = app.dependency_overrides.get(get_notification_settings)
    app.dependency_overrides[get_notification_settings] = lambda: NotificationConfig()
    try:
        response = await client.get(f"{SERVICE_ROOT}/health")
    finally:
        if previous_settings is None:
            app.dependency_overrides.pop(get_notification_settings, None)
        else:
            app.dependency_overrides[get_notification_settings] = previous_settings
    assert response.status_code == 503


async def test_claim_acknowledgement_and_stale_lease(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    student, intent = await _create_assignment_intent(db_session)
    headers = {"X-Notification-Service-Key": notification_api_key}
    claimed = await client.post(f"{SERVICE_ROOT}/claim", headers=headers, json={})
    assert claimed.status_code == 200, claimed.text
    item = claimed.json()["items"][0]
    assert item["id"] == str(intent.id)
    assert item["recipient_id"] == str(student.id)
    assert item["chat_id"] == student.telegram_private_chat_id
    assert item["notification_type"] == NotificationType.ASSIGNMENT_CREATED
    assert item["source_id"] == str(intent.source_id)

    ack = await client.post(
        f"{SERVICE_ROOT}/{intent.id}/ack",
        headers=headers,
        json={"lease_token": item["lease_token"], "outcome": "sent"},
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["state"] == NotificationState.SENT

    stale = await client.post(
        f"{SERVICE_ROOT}/{intent.id}/ack",
        headers=headers,
        json={"lease_token": item["lease_token"], "outcome": "sent"},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"] == "notification_lease_stale"


async def test_disabled_or_inactive_recipients_are_suppressed(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    _, disabled = await _create_assignment_intent(
        db_session, notification_enabled=False
    )
    _, inactive = await _create_assignment_intent(db_session, recipient_active=False)
    _, unroutable = await _create_assignment_intent(db_session, chat_id=None)
    headers = {"X-Notification-Service-Key": notification_api_key}
    claim = await client.post(f"{SERVICE_ROOT}/claim", headers=headers, json={})
    assert claim.status_code == 200, claim.text
    assert claim.json()["items"] == []

    await db_session.refresh(disabled)
    await db_session.refresh(inactive)
    assert disabled.state == NotificationState.SUPPRESSED
    assert disabled.last_error_code == "assignment_notification_disabled"
    assert inactive.state == NotificationState.SUPPRESSED
    assert inactive.last_error_code == "recipient_inactive"
    await db_session.refresh(unroutable)
    assert unroutable.state == NotificationState.SUPPRESSED
    assert unroutable.last_error_code == "recipient_unroutable"


async def test_assignment_alert_survives_completion_before_dispatch(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    _, intent = await _create_assignment_intent(db_session, assignment_completed=True)
    claim = await client.post(
        f"{SERVICE_ROOT}/claim",
        headers={"X-Notification-Service-Key": notification_api_key},
        json={},
    )
    assert claim.status_code == 200, claim.text
    assert [item["id"] for item in claim.json()["items"]] == [str(intent.id)]


async def test_submission_alert_survives_submission_completion(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    homework_id = uuid.uuid4()
    teacher = User(
        id=teacher_id,
        email=f"teacher-{teacher_id}@example.com",
        role="TEACHER",
        timezone="UTC",
        telegram_private_chat_id=123456789,
    )
    student = User(
        id=student_id,
        email=f"student-{student_id}@example.com",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher_id,
    )
    teacher_profile = TeacherProfile(teacher_id=teacher_id, code="t_SUB123")
    homework = Homework(
        id=homework_id,
        teacher_id=teacher_id,
        student_id=student_id,
        text="Complete the exercise",
        status="COMPLETE",
        completion_source="STUDENT_SUBMISSION",
        completed_at=NOW,
        operation_key=uuid.uuid4(),
    )
    submission = HomeworkSubmission(
        id=uuid.uuid4(),
        homework_id=homework_id,
        student_id=student_id,
        version=1,
        text="Finished",
        operation_key=uuid.uuid4(),
    )
    intent = NotificationIntent(
        id=uuid.uuid4(),
        recipient_id=teacher_id,
        notification_type=NotificationType.SUBMISSION_RECEIVED,
        dedupe_key=f"submission:{teacher_id}:{homework_id}:1",
        source_id=homework_id,
        source_version=1,
        scheduled_at=NOW,
        available_at=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    db_session.add_all(
        [teacher, student, teacher_profile, homework, submission, intent]
    )
    await db_session.flush()

    claim = await client.post(
        f"{SERVICE_ROOT}/claim",
        headers={"X-Notification-Service-Key": notification_api_key},
        json={},
    )
    assert claim.status_code == 200, claim.text
    assert [item["id"] for item in claim.json()["items"]] == [str(intent.id)]


async def test_completed_homework_reminder_is_suppressed(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    _, intent = await _create_assignment_intent(db_session, assignment_completed=True)
    intent.notification_type = NotificationType.HOMEWORK_REMINDER
    profile = await db_session.scalar(
        select(StudentProfile).where(StudentProfile.student_id == intent.recipient_id)
    )
    assert profile is not None
    profile.notification_homework = 2
    await db_session.flush()

    claim = await client.post(
        f"{SERVICE_ROOT}/claim",
        headers={"X-Notification-Service-Key": notification_api_key},
        json={},
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["items"] == []
    await db_session.refresh(intent)
    assert intent.state == NotificationState.SUPPRESSED
    assert intent.last_error_code == "homework_complete"


@pytest.mark.parametrize("hidden_by_vacation", [False, True])
async def test_lesson_claim_revalidates_the_exact_visible_occurrence(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
    hidden_by_vacation: bool,
) -> None:
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    lesson_id = uuid.uuid4()
    teacher = User(
        id=teacher_id,
        email=f"teacher-{teacher_id}@example.com",
        role="TEACHER",
        timezone="UTC",
    )
    student = User(
        id=student_id,
        email=f"student-{student_id}@example.com",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher_id,
        telegram_private_chat_id=123456789,
    )
    profile = StudentProfile(student_id=student_id, notification_lesson=15)
    lesson_start = NOW + timedelta(minutes=10)
    lesson = Event(
        id=lesson_id,
        teacher_id=teacher_id,
        user_id=student_id,
        event_type=EventType.LESSON,
        start=lesson_start.replace(tzinfo=None),
        end=(lesson_start + timedelta(minutes=30)).replace(tzinfo=None),
    )
    intent = NotificationIntent(
        id=uuid.uuid4(),
        recipient_id=student_id,
        notification_type=NotificationType.LESSON_REMINDER,
        dedupe_key=f"lesson:{student_id}:{lesson_id}:{lesson_start.isoformat()}",
        occurrence_source_type=OccurrenceSourceKind.EVENT.value,
        occurrence_source_id=lesson_id,
        occurrence_start_at=lesson_start,
        scheduled_at=NOW,
        available_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )
    db_session.add_all([teacher, student, profile, lesson, intent])
    if hidden_by_vacation:
        db_session.add(
            Event(
                teacher_id=teacher_id,
                user_id=student_id,
                event_type=EventType.VACATION,
                start=(lesson_start - timedelta(minutes=1)).replace(tzinfo=None),
                end=(lesson_start + timedelta(minutes=1)).replace(tzinfo=None),
            )
        )
    await db_session.flush()

    claim = await client.post(
        f"{SERVICE_ROOT}/claim",
        headers={"X-Notification-Service-Key": notification_api_key},
        json={},
    )
    assert claim.status_code == 200, claim.text
    if hidden_by_vacation:
        assert claim.json()["items"] == []
        await db_session.refresh(intent)
        assert intent.state == NotificationState.SUPPRESSED
        assert intent.last_error_code == "lesson_occurrence_stale"
    else:
        assert [item["id"] for item in claim.json()["items"]] == [str(intent.id)]
        assert claim.json()["items"][0]["occurrence_source_type"] == "event"


async def test_retry_after_is_honored_and_expired_lease_is_reclaimed(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    _, intent = await _create_assignment_intent(db_session)
    headers = {"X-Notification-Service-Key": notification_api_key}
    claim = await client.post(f"{SERVICE_ROOT}/claim", headers=headers, json={})
    item = claim.json()["items"][0]

    retry = await client.post(
        f"{SERVICE_ROOT}/{intent.id}/ack",
        headers=headers,
        json={
            "lease_token": item["lease_token"],
            "outcome": "retryable",
            "error_code": "telegram_429",
            "retry_after_seconds": 20,
        },
    )
    assert retry.status_code == 200, retry.text
    retry_at = datetime.fromisoformat(retry.json()["available_at"])
    assert retry_at >= NOW + timedelta(seconds=20)
    too_early = await client.post(f"{SERVICE_ROOT}/claim", headers=headers, json={})
    assert too_early.json()["items"] == []

    intent.state = NotificationState.CLAIMED
    intent.lease_token = uuid.uuid4()
    intent.lease_expires_at = NOW - timedelta(seconds=1)
    intent.available_at = NOW
    intent.attempt_count = 1
    await db_session.commit()
    reclaimed = await client.post(f"{SERVICE_ROOT}/claim", headers=headers, json={})
    reclaimed_item = reclaimed.json()["items"][0]
    assert reclaimed_item["lease_token"] != str(item["lease_token"])
    await db_session.refresh(intent)
    assert intent.attempt_count == 2
    assert intent.last_outcome == AcknowledgementOutcome.UNKNOWN.name

    stale = await client.post(
        f"{SERVICE_ROOT}/{intent.id}/ack",
        headers=headers,
        json={"lease_token": item["lease_token"], "outcome": "sent"},
    )
    assert stale.status_code == 409, stale.text


async def test_retry_attempt_limit_becomes_terminal(
    client: AsyncClient,
    db_session: AsyncSession,
    notification_api_key: str,
) -> None:
    _, intent = await _create_assignment_intent(db_session)
    intent.attempt_count = 7
    await db_session.commit()
    headers = {"X-Notification-Service-Key": notification_api_key}
    claim = await client.post(f"{SERVICE_ROOT}/claim", headers=headers, json={})
    item = claim.json()["items"][0]
    ack = await client.post(
        f"{SERVICE_ROOT}/{intent.id}/ack",
        headers=headers,
        json={
            "lease_token": item["lease_token"],
            "outcome": "unknown",
            "error_code": "send_outcome_unknown",
        },
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["state"] == NotificationState.TERMINAL
    await db_session.refresh(intent)
    assert intent.last_outcome == AcknowledgementOutcome.UNKNOWN.name


@pytest.fixture(params=("sqlite", "postgresql"))
async def notification_session_factory(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    if request.param == "sqlite":
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'notification-claims.db'}",
            connect_args={"timeout": 10},
            poolclass=NullPool,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            yield factory
        finally:
            await engine.dispose()
        return

    database_url = os.environ.get("SCHEDULE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SCHEDULE_TEST_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.fail("SCHEDULE_TEST_DATABASE_URL must use postgresql+psycopg")
    schema = f"notification_claim_{uuid.uuid4().hex}"
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


async def test_concurrent_claimers_never_share_an_active_lease(
    notification_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with notification_session_factory() as session:
        for _ in range(4):
            await _create_assignment_intent(session)
        await session.commit()

    async def claim_batch() -> list[uuid.UUID]:
        async with notification_session_factory() as session:
            results = await claim_pending_intents(session, now=NOW, limit=2)
            return [item.id for item in results]

    first, second = await asyncio.gather(claim_batch(), claim_batch())
    assert len(first) == len(set(first))
    assert len(second) == len(set(second))
    assert set(first).isdisjoint(second)
    assert len(first) + len(second) == 4


async def test_producer_runner_uses_a_bounded_tick_and_stops_cleanly(
    notification_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    stop = asyncio.Event()
    produced_at: list[datetime] = []

    async def producer(_session: AsyncSession, now: datetime) -> None:
        produced_at.append(now)
        stop.set()

    await run_producer_runner(
        (producer,),
        stop_event=stop,
        poll_seconds=30,
        session_factory=notification_session_factory,
    )
    assert len(produced_at) == 1
    assert produced_at[0].tzinfo is not None

    with pytest.raises(RuntimeError, match="No notification producers"):
        await run_producer_runner(
            (),
            stop_event=asyncio.Event(),
            session_factory=notification_session_factory,
        )
