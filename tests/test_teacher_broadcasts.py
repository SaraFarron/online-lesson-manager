import asyncio
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.auth.models import User
from backend.auth.utils import create_access_token
from backend.models import Base
from backend.notifications.broadcasts import (
    preview_teacher_broadcast,
    publish_teacher_broadcast,
)
from backend.notifications.constants import (
    AcknowledgementOutcome,
    NotificationState,
    NotificationType,
)
from backend.notifications.models import NotificationIntent, TeacherBroadcast
from backend.notifications.schemas import (
    AcknowledgeRequest,
    TeacherBroadcastPublishRequest,
)
from backend.notifications.service import acknowledge_intent, claim_pending_intents

BROADCAST_ROOT = "/api/v1/notifications/broadcasts"


async def _user(
    session: AsyncSession,
    role: str,
    *,
    teacher_id: UUID | None = None,
    chat_id: int | None = None,
    active: bool = True,
) -> User:
    user_id = uuid4()
    user = User(
        id=user_id,
        email=f"broadcast-{user_id}@example.com",
        full_name=f"Broadcast {role}",
        role=role,
        timezone="UTC",
        teacher_id=teacher_id,
        telegram_private_chat_id=chat_id,
        is_active=active,
    )
    session.add(user)
    await session.commit()
    return user


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


def _idempotency(operation_key: UUID) -> dict[str, str]:
    return {"Idempotency-Key": str(operation_key)}


@pytest.fixture
async def broadcast_postgres_session_factory() -> AsyncGenerator[
    async_sessionmaker[AsyncSession]
]:
    database_url = os.environ.get("SCHEDULE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SCHEDULE_TEST_DATABASE_URL is not configured")
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.fail("SCHEDULE_TEST_DATABASE_URL must use postgresql+psycopg")
    schema = f"teacher_broadcast_test_{uuid4().hex}"
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


async def test_preview_counts_routable_students_without_creating_a_broadcast(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    await _user(db_session, "STUDENT", teacher_id=teacher.id, chat_id=123)
    await _user(db_session, "STUDENT", teacher_id=teacher.id)
    await _user(db_session, "STUDENT", teacher_id=teacher.id, active=False)

    response = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": "Tomorrow's lesson is moved."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["recipient_count"] == 2
    assert response.json()["eligible_count"] == 1
    assert response.json()["unsendable_count"] == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(TeacherBroadcast)) == 0
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(NotificationIntent))
        == 0
    )


async def test_publish_queues_one_intent_per_student_and_reconciles_retries(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    students = [
        await _user(db_session, "STUDENT", teacher_id=teacher.id, chat_id=123),
        await _user(db_session, "STUDENT", teacher_id=teacher.id),
    ]
    text = "Please check this: <math & science>"
    preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": text},
    )
    assert preview.status_code == 200, preview.text
    operation_key = uuid4()
    request = {"text": text, "preview_token": preview.json()["preview_token"]}
    headers = {**_auth(teacher), **_idempotency(operation_key)}

    published = await client.post(
        BROADCAST_ROOT,
        headers=headers,
        json=request,
    )

    assert published.status_code == 202, published.text
    result = published.json()
    assert result["status"] == "queued"
    assert result["recipient_count"] == 2
    assert result["eligible_count"] == 1
    assert result["unsendable_count"] == 1
    intents = list(
        await db_session.scalars(
            select(NotificationIntent).order_by(NotificationIntent.recipient_id)
        )
    )
    assert {intent.recipient_id for intent in intents} == {
        student.id for student in students
    }
    assert all(
        intent.notification_type == NotificationType.TEACHER_BROADCAST
        and intent.source_id == UUID(result["id"])
        for intent in intents
    )

    retried = await client.post(BROADCAST_ROOT, headers=headers, json=request)
    reconciled = await client.get(
        f"{BROADCAST_ROOT}/operations/{operation_key}",
        headers=_auth(teacher),
    )

    assert retried.status_code == 202
    assert retried.json() == result
    assert reconciled.status_code == 200
    assert reconciled.json() == result
    assert (
        await db_session.scalar(select(func.count()).select_from(TeacherBroadcast)) == 1
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(NotificationIntent))
        == 2
    )

    reused_key = await client.post(
        BROADCAST_ROOT,
        headers=headers,
        json={"text": "Different message", "preview_token": request["preview_token"]},
    )
    assert reused_key.status_code == 409
    assert reused_key.json()["detail"] == "broadcast_operation_key_reused"

    fresh_preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": text},
    )
    second_publication = await client.post(
        BROADCAST_ROOT,
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={"text": text, "preview_token": fresh_preview.json()["preview_token"]},
    )
    assert second_publication.status_code == 202
    assert second_publication.json()["id"] != result["id"]
    assert (
        await db_session.scalar(select(func.count()).select_from(TeacherBroadcast)) == 2
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(NotificationIntent))
        == 4
    )


async def test_publish_fans_out_across_roster_pages(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    students = []
    for index in range(105):
        student_id = uuid4()
        students.append(
            User(
                id=student_id,
                email=f"broadcast-page-{student_id}@example.com",
                role="STUDENT",
                timezone="UTC",
                teacher_id=teacher.id,
                telegram_private_chat_id=100000 + index,
                is_active=True,
            )
        )
    db_session.add_all(students)
    await db_session.commit()

    text = "Roster-wide message"
    preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": text},
    )
    operation_key = uuid4()
    published = await client.post(
        BROADCAST_ROOT,
        headers={**_auth(teacher), **_idempotency(operation_key)},
        json={"text": text, "preview_token": preview.json()["preview_token"]},
    )

    assert preview.status_code == 200
    assert preview.json()["recipient_count"] == 105
    assert published.status_code == 202, published.text
    assert published.json()["recipient_count"] == 105
    recipients = set(
        await db_session.scalars(
            select(NotificationIntent.recipient_id).where(
                NotificationIntent.notification_type
                == NotificationType.TEACHER_BROADCAST
            )
        )
    )
    assert recipients == {student.id for student in students}


async def test_publish_rejects_changed_text_and_roster(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    await _user(db_session, "STUDENT", teacher_id=teacher.id, chat_id=123)
    headers = _auth(teacher)
    preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=headers,
        json={"text": "Original message"},
    )
    preview_token = preview.json()["preview_token"]

    changed_text = await client.post(
        BROADCAST_ROOT,
        headers={**headers, **_idempotency(uuid4())},
        json={"text": "Changed message", "preview_token": preview_token},
    )
    assert changed_text.status_code == 409
    assert changed_text.json()["detail"] == "broadcast_preview_stale"

    await _user(db_session, "STUDENT", teacher_id=teacher.id, chat_id=456)
    changed_roster = await client.post(
        BROADCAST_ROOT,
        headers={**headers, **_idempotency(uuid4())},
        json={"text": "Original message", "preview_token": preview_token},
    )
    assert changed_roster.status_code == 409
    assert changed_roster.json()["detail"] == "broadcast_preview_stale"
    assert (
        await db_session.scalar(select(func.count()).select_from(NotificationIntent))
        == 0
    )


async def test_preview_rejects_blank_and_overlong_messages(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    for text in (" \n ", "x" * 4097):
        response = await client.post(
            f"{BROADCAST_ROOT}/preview",
            headers=_auth(teacher),
            json={"text": text},
        )
        assert response.status_code == 422


async def test_student_cannot_preview_and_teacher_cannot_read_other_publication(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    other_teacher = await _user(db_session, "TEACHER")
    student = await _user(db_session, "STUDENT", teacher_id=teacher.id)
    forbidden = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(student),
        json={"text": "Not allowed"},
    )
    assert forbidden.status_code == 403
    forbidden_publish = await client.post(
        BROADCAST_ROOT,
        headers={**_auth(student), **_idempotency(uuid4())},
        json={"text": "Not allowed", "preview_token": "not-a-preview"},
    )
    assert forbidden_publish.status_code == 403

    preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": "Private message"},
    )
    operation_key = uuid4()
    published = await client.post(
        BROADCAST_ROOT,
        headers={**_auth(teacher), **_idempotency(operation_key)},
        json={
            "text": "Private message",
            "preview_token": preview.json()["preview_token"],
        },
    )
    assert published.status_code == 202

    not_found = await client.get(
        f"{BROADCAST_ROOT}/operations/{operation_key}",
        headers=_auth(other_teacher),
    )
    assert not_found.status_code == 404
    results_not_found = await client.get(
        f"{BROADCAST_ROOT}/operations/{operation_key}/results",
        headers=_auth(other_teacher),
    )
    assert results_not_found.status_code == 404


async def test_broadcast_results_aggregate_delivery_states_and_paginate(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    students = [
        await _user(
            db_session,
            "STUDENT",
            teacher_id=teacher.id,
            chat_id=1000 + index,
        )
        for index in range(8)
    ]
    for index, student in enumerate(students):
        student.full_name = f"Student {index:02}"
    await db_session.commit()

    text = "Result status test"
    preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": text},
    )
    operation_key = uuid4()
    published = await client.post(
        BROADCAST_ROOT,
        headers={**_auth(teacher), **_idempotency(operation_key)},
        json={"text": text, "preview_token": preview.json()["preview_token"]},
    )
    assert published.status_code == 202, published.text

    intents = list(
        await db_session.scalars(
            select(NotificationIntent).where(
                NotificationIntent.source_id == UUID(published.json()["id"])
            )
        )
    )
    state_by_name = {
        "Student 00": (NotificationState.PENDING, None),
        "Student 01": (NotificationState.CLAIMED, None),
        "Student 02": (NotificationState.RETRY, "RETRYABLE"),
        "Student 03": (NotificationState.RETRY, "UNKNOWN"),
        "Student 04": (NotificationState.SENT, "SENT"),
        "Student 05": (NotificationState.SUPPRESSED, "TERMINAL"),
        "Student 06": (NotificationState.TERMINAL, "TERMINAL"),
        "Student 07": (NotificationState.TERMINAL, "UNKNOWN"),
    }
    users_by_id = {student.id: student for student in students}
    for intent in intents:
        student = users_by_id[intent.recipient_id]
        intent.state, intent.last_outcome = state_by_name[student.full_name]
        if intent.state == NotificationState.CLAIMED:
            intent.lease_token = uuid4()
            intent.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    await db_session.commit()

    response = await client.get(
        f"{BROADCAST_ROOT}/operations/{operation_key}/results?skip=2&limit=3",
        headers=_auth(teacher),
    )

    assert response.status_code == 200, response.text
    results = response.json()
    assert results["total"] == 8
    assert results["skip"] == 2
    assert results["limit"] == 3
    assert results["counts"] == {
        "pending": 2,
        "retrying": 1,
        "sent": 1,
        "suppressed": 1,
        "failed": 1,
        "unknown": 2,
    }
    assert [recipient["student_name"] for recipient in results["recipients"]] == [
        "Student 02",
        "Student 03",
        "Student 04",
    ]
    assert [recipient["status"] for recipient in results["recipients"]] == [
        "retrying",
        "unknown",
        "sent",
    ]
    assert "chat_id" not in response.text

    invalid_limit = await client.get(
        f"{BROADCAST_ROOT}/operations/{operation_key}/results?limit=101",
        headers=_auth(teacher),
    )
    assert invalid_limit.status_code == 422


async def test_empty_broadcast_has_zero_results(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": "No recipients"},
    )
    operation_key = uuid4()
    published = await client.post(
        BROADCAST_ROOT,
        headers={**_auth(teacher), **_idempotency(operation_key)},
        json={
            "text": "No recipients",
            "preview_token": preview.json()["preview_token"],
        },
    )
    assert published.status_code == 202

    results = await client.get(
        f"{BROADCAST_ROOT}/operations/{operation_key}/results",
        headers=_auth(teacher),
    )

    assert results.status_code == 200, results.text
    assert results.json()["total"] == 0
    assert results.json()["recipients"] == []
    assert results.json()["counts"] == {
        "pending": 0,
        "retrying": 0,
        "sent": 0,
        "suppressed": 0,
        "failed": 0,
        "unknown": 0,
    }


async def test_delivery_acknowledgements_appear_in_broadcast_results(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    students = [
        await _user(
            db_session,
            "STUDENT",
            teacher_id=teacher.id,
            chat_id=2000 + index,
        )
        for index in range(3)
    ]
    text = "Delivery outcomes"
    preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": text},
    )
    operation_key = uuid4()
    published = await client.post(
        BROADCAST_ROOT,
        headers={**_auth(teacher), **_idempotency(operation_key)},
        json={"text": text, "preview_token": preview.json()["preview_token"]},
    )
    assert published.status_code == 202

    claimed = await claim_pending_intents(
        db_session,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    outcomes = {
        students[0].id: (AcknowledgementOutcome.RETRYABLE, "telegram_rate_limited"),
        students[1].id: (AcknowledgementOutcome.TERMINAL, "telegram_forbidden"),
        students[2].id: (AcknowledgementOutcome.UNKNOWN, "telegram_send_unknown"),
    }
    for notification in claimed:
        outcome, error_code = outcomes[notification.recipient_id]
        await acknowledge_intent(
            db_session,
            notification.id,
            AcknowledgeRequest(
                lease_token=notification.lease_token,
                outcome=outcome,
                error_code=error_code,
            ),
            now=datetime.now(UTC) + timedelta(seconds=2),
        )

    results = await client.get(
        f"{BROADCAST_ROOT}/operations/{operation_key}/results",
        headers=_auth(teacher),
    )

    assert results.status_code == 200, results.text
    assert results.json()["counts"] == {
        "pending": 0,
        "retrying": 1,
        "sent": 0,
        "suppressed": 0,
        "failed": 1,
        "unknown": 1,
    }
    assert {item["status"] for item in results.json()["recipients"]} == {
        "retrying",
        "failed",
        "unknown",
    }


async def test_claim_revalidates_link_and_builds_the_teacher_message(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER")
    deliverable = await _user(db_session, "STUDENT", teacher_id=teacher.id, chat_id=123)
    unlinked = await _user(db_session, "STUDENT", teacher_id=teacher.id, chat_id=456)
    text = "Please check: <math & science>"
    preview = await client.post(
        f"{BROADCAST_ROOT}/preview",
        headers=_auth(teacher),
        json={"text": text},
    )
    operation_key = uuid4()
    published = await client.post(
        BROADCAST_ROOT,
        headers={**_auth(teacher), **_idempotency(operation_key)},
        json={
            "text": text,
            "preview_token": preview.json()["preview_token"],
        },
    )
    assert published.status_code == 202
    unlinked.teacher_id = None
    await db_session.commit()

    claimed = await claim_pending_intents(
        db_session,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert len(claimed) == 1
    assert claimed[0].recipient_id == deliverable.id
    assert claimed[0].notification_type == NotificationType.TEACHER_BROADCAST
    assert claimed[0].content == [text]
    suppressed = await db_session.scalar(
        select(NotificationIntent).where(NotificationIntent.recipient_id == unlinked.id)
    )
    assert suppressed is not None
    assert suppressed.state == NotificationState.SUPPRESSED
    results = await client.get(
        f"{BROADCAST_ROOT}/operations/{operation_key}/results",
        headers=_auth(teacher),
    )
    assert results.status_code == 200, results.text
    assert results.json()["counts"]["pending"] == 1
    assert results.json()["counts"]["suppressed"] == 1


async def test_concurrent_publish_retries_create_one_broadcast(
    broadcast_postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with broadcast_postgres_session_factory() as session:
        teacher = await _user(session, "TEACHER")
        await _user(session, "STUDENT", teacher_id=teacher.id, chat_id=123)
        preview = await preview_teacher_broadcast(session, teacher, "One message")
        teacher_id = teacher.id

    operation_key = uuid4()
    request = TeacherBroadcastPublishRequest(
        text="One message",
        preview_token=preview.preview_token,
    )

    async def publish_once() -> UUID:
        async with broadcast_postgres_session_factory() as session:
            teacher = await session.get(User, teacher_id)
            assert teacher is not None
            result = await publish_teacher_broadcast(
                session,
                teacher,
                request,
                operation_key,
            )
            return result.id

    first, second = await asyncio.gather(publish_once(), publish_once())
    assert first == second
    async with broadcast_postgres_session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(TeacherBroadcast))
            == 1
        )
        assert (
            await session.scalar(select(func.count()).select_from(NotificationIntent))
            == 1
        )
