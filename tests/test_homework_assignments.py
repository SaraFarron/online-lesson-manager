from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.auth.utils import create_access_token
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.time_utils import to_storage_utc
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.constants import (
    MAX_ASSIGNMENT_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_BYTES,
)
from backend.homework.models import Homework, HomeworkAttachment
from backend.homework.schemas import HomeworkCreate
from backend.homework.services.assignments import create_assignment
from backend.main import app


@pytest.fixture
def homework_storage(tmp_path: Path) -> Generator[Path]:
    config = HomeworkConfig(STORAGE_PATH=tmp_path)
    app.dependency_overrides[get_homework_settings] = lambda: config
    try:
        yield tmp_path
    finally:
        app.dependency_overrides.pop(get_homework_settings, None)


async def _user(session: AsyncSession, role: str = "TEACHER") -> User:
    user = User(
        id=uuid4(),
        email=f"assignment-{uuid4()}@example.com",
        role=role,
        timezone="UTC",
    )
    session.add(user)
    await session.commit()
    return user


async def _linked_pair(session: AsyncSession) -> tuple[User, User]:
    teacher = await _user(session)
    student = await _user(session, "STUDENT")
    student.teacher_id = teacher.id
    await session.commit()
    return teacher, student


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


def _idempotency(key: UUID) -> dict[str, str]:
    return {"Idempotency-Key": str(key)}


async def _stage_metadata(
    session: AsyncSession,
    owner: User,
    byte_size: int,
) -> HomeworkAttachment:
    attachment = HomeworkAttachment(
        id=uuid4(),
        owner_user_id=owner.id,
        storage_key=uuid4().hex,
        file_name="staged.bin",
        byte_size=byte_size,
        staged_until=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(attachment)
    await session.commit()
    return attachment


def _recurrent_lesson(
    teacher: User,
    student: User,
    starts_at: datetime,
) -> RecurrentEvent:
    return RecurrentEvent(
        id=uuid4(),
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(starts_at),
        end=to_storage_utc(starts_at + timedelta(minutes=45)),
        interval=7,
        interval_end=to_storage_utc(starts_at + timedelta(days=30)),
        recurrence_mode="UTC_STEP",
        recurrence_timezone=None,
    )


async def test_create_text_only_assignment_and_reconcile_retries(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    operation_key = uuid4()
    payload = {"student_id": str(student.id), "text": "Read chapter 3"}
    headers = {**_auth(teacher), **_idempotency(operation_key)}

    created = await client.post(
        "/api/v1/homework/assignments", headers=headers, json=payload
    )
    assert created.status_code == 201
    assert created.json()["status"] == "INCOMPLETE"
    assert created.json()["material_count"] == 0

    retry = await client.post(
        "/api/v1/homework/assignments", headers=headers, json=payload
    )
    assert retry.status_code == 201
    assert retry.json() == created.json()
    assert await db_session.scalar(select(func.count()).select_from(Homework)) == 1

    mismatched_retry = await client.post(
        "/api/v1/homework/assignments",
        headers=headers,
        json={**payload, "text": "Different content"},
    )
    assert mismatched_retry.status_code == 409

    empty = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={"student_id": str(student.id)},
    )
    assert empty.status_code == 422


async def test_create_file_only_and_mixed_assignments(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    teacher, student = await _linked_pair(db_session)

    staged_file = await client.post(
        "/api/v1/homework/attachments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        files={"file": ("lesson.pdf", b"lesson material")},
    )
    assert staged_file.status_code == 201
    file_only = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={
            "student_id": str(student.id),
            "attachment_ids": [staged_file.json()["id"]],
        },
    )
    assert file_only.status_code == 201
    assert file_only.json()["material_count"] == 1

    staged_mixed = await client.post(
        "/api/v1/homework/attachments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        files={"file": ("diagram.png", b"diagram")},
    )
    mixed = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={
            "student_id": str(student.id),
            "text": "Explain this diagram",
            "attachment_ids": [staged_mixed.json()["id"]],
        },
    )
    assert mixed.status_code == 201
    assert mixed.json()["material_count"] == 1

    linked = list(
        (
            await db_session.scalars(
                select(HomeworkAttachment).where(
                    HomeworkAttachment.homework_id == UUID(file_only.json()["id"])
                )
            )
        ).all()
    )
    assert len(linked) == 1
    assert linked[0].staged_until is None
    download = await client.get(
        f"/api/v1/homework/attachments/{linked[0].id}",
        headers=_auth(student),
    )
    assert download.status_code == 200
    assert download.content == b"lesson material"
    assert len(list(homework_storage.iterdir())) == 2


async def test_assignment_rejects_unlinked_students_and_foreign_uploads(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    other_teacher, other_student = await _linked_pair(db_session)
    staged = await client.post(
        "/api/v1/homework/attachments",
        headers={**_auth(other_teacher), **_idempotency(uuid4())},
        files={"file": ("foreign.txt", b"private")},
    )
    attachment_id = staged.json()["id"]

    unlinked_student = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={"student_id": str(other_student.id), "text": "Not allowed"},
    )
    assert unlinked_student.status_code == 403

    foreign_upload = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={
            "student_id": str(student.id),
            "attachment_ids": [attachment_id],
        },
    )
    assert foreign_upload.status_code == 404
    assert await db_session.scalar(select(func.count()).select_from(Homework)) == 0
    attachment = await db_session.get(HomeworkAttachment, UUID(attachment_id))
    assert attachment is not None
    assert attachment.homework_id is None

    student_attempt = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(student), **_idempotency(uuid4())},
        json={"student_id": str(student.id), "text": "Not a teacher"},
    )
    assert student_attempt.status_code == 403


async def test_lesson_reference_requires_an_active_exact_lesson_occurrence(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    other_student = await _user(db_session, "STUDENT")
    now = datetime.now(UTC)
    one_off_start = now + timedelta(days=2)
    one_off = Event(
        id=uuid4(),
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(one_off_start),
        end=to_storage_utc(one_off_start + timedelta(minutes=60)),
    )
    recurrent_start = now + timedelta(days=3)
    recurrent = _recurrent_lesson(teacher, student, recurrent_start)
    cancelled_start = now + timedelta(days=4)
    cancelled_series = _recurrent_lesson(teacher, student, cancelled_start)
    cancelled = RecurrentCancels(
        id=uuid4(),
        recurrent_event_id=cancelled_series.id,
        start=to_storage_utc(cancelled_start),
        end=to_storage_utc(cancelled_start + timedelta(minutes=45)),
    )
    wrong_student_start = now + timedelta(days=5)
    wrong_student = Event(
        id=uuid4(),
        teacher_id=teacher.id,
        user_id=other_student.id,
        event_type=EventType.LESSON.value,
        start=to_storage_utc(wrong_student_start),
        end=to_storage_utc(wrong_student_start + timedelta(minutes=60)),
    )
    break_start = now + timedelta(days=6)
    break_event = Event(
        id=uuid4(),
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.BREAK.value,
        start=to_storage_utc(break_start),
        end=to_storage_utc(break_start + timedelta(minutes=10)),
    )
    db_session.add_all(
        [
            one_off,
            recurrent,
            cancelled_series,
            cancelled,
            wrong_student,
            break_event,
        ]
    )
    await db_session.commit()

    valid_references = [
        {
            "kind": "EVENT",
            "source_id": str(one_off.id),
            "starts_at": one_off_start.isoformat(),
        },
        {
            "kind": "RECURRENT_EVENT",
            "source_id": str(recurrent.id),
            "starts_at": recurrent_start.isoformat(),
        },
    ]
    for reference in valid_references:
        response = await client.post(
            "/api/v1/homework/assignments",
            headers={**_auth(teacher), **_idempotency(uuid4())},
            json={
                "student_id": str(student.id),
                "text": "Prepare for the lesson",
                "lesson_occurrence": reference,
            },
        )
        assert response.status_code == 201, response.text

    invalid_references = [
        {
            "kind": "EVENT",
            "source_id": str(uuid4()),
            "starts_at": one_off_start.isoformat(),
        },
        {
            "kind": "EVENT",
            "source_id": str(recurrent.id),
            "starts_at": recurrent_start.isoformat(),
        },
        {
            "kind": "RECURRENT_EVENT",
            "source_id": str(cancelled_series.id),
            "starts_at": cancelled_start.isoformat(),
        },
        {
            "kind": "EVENT",
            "source_id": str(break_event.id),
            "starts_at": break_start.isoformat(),
        },
        {
            "kind": "EVENT",
            "source_id": str(wrong_student.id),
            "starts_at": wrong_student_start.isoformat(),
        },
        {
            "kind": "EVENT",
            "source_id": str(one_off.id),
            "starts_at": (now - timedelta(days=1)).isoformat(),
        },
    ]
    for reference in invalid_references:
        response = await client.post(
            "/api/v1/homework/assignments",
            headers={**_auth(teacher), **_idempotency(uuid4())},
            json={
                "student_id": str(student.id),
                "text": "Invalid lesson reference",
                "lesson_occurrence": reference,
            },
        )
        assert response.status_code == 422, response.text

    assignments = list((await db_session.scalars(select(Homework))).all())
    assert len(assignments) == 2
    assert {assignment.lesson_kind for assignment in assignments} == {
        "EVENT",
        "RECURRENT_EVENT",
    }


async def test_assignment_enforces_five_file_and_fifty_mib_limits(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    exact_limit_sizes = [
        MAX_ATTACHMENT_BYTES,
        MAX_ATTACHMENT_BYTES,
        MAX_ASSIGNMENT_ATTACHMENT_BYTES - 2 * MAX_ATTACHMENT_BYTES - 2,
        1,
        1,
    ]
    exact_limit = [
        await _stage_metadata(db_session, teacher, size) for size in exact_limit_sizes
    ]
    accepted = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={
            "student_id": str(student.id),
            "attachment_ids": [str(item.id) for item in exact_limit],
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["material_count"] == 5

    over_limit_sizes = [
        MAX_ATTACHMENT_BYTES,
        MAX_ATTACHMENT_BYTES,
        10 * 1024 * 1024 + 1,
    ]
    over_limit = [
        await _stage_metadata(db_session, teacher, size) for size in over_limit_sizes
    ]
    rejected = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={
            "student_id": str(student.id),
            "attachment_ids": [str(item.id) for item in over_limit],
        },
    )
    assert rejected.status_code == 413
    assert rejected.json()["detail"] == "Assignment attachments exceed the 50 MiB limit"
    assert await db_session.scalar(select(func.count()).select_from(Homework)) == 1
    for item in over_limit:
        staged = await db_session.get(HomeworkAttachment, item.id)
        assert staged is not None
        assert staged.homework_id is None

    too_many_ids = [str(uuid4()) for _ in range(6)]
    too_many = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), **_idempotency(uuid4())},
        json={"student_id": str(student.id), "attachment_ids": too_many_ids},
    )
    assert too_many.status_code == 422


async def test_failed_assignment_finalization_leaves_staging_unchanged(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teacher, student = await _linked_pair(db_session)
    attachment = await _stage_metadata(db_session, teacher, 12)
    attachment_id = attachment.id

    async def fail_commit() -> None:
        raise SQLAlchemyError("simulated database failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(HTTPException) as raised:
        await create_assignment(
            db_session,
            teacher,
            HomeworkCreate(
                student_id=student.id,
                attachment_ids=[attachment.id],
            ),
            uuid4(),
        )

    assert raised.value.status_code == 503
    assert await db_session.scalar(select(func.count()).select_from(Homework)) == 0
    staged = await db_session.get(HomeworkAttachment, attachment_id)
    assert staged is not None
    assert staged.homework_id is None
    assert staged.staged_until is not None


async def test_unknown_commit_outcome_returns_original_assignment(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teacher, student = await _linked_pair(db_session)
    original_commit = db_session.commit

    async def commit_then_lose_acknowledgement() -> None:
        await original_commit()
        raise SQLAlchemyError("simulated lost commit acknowledgement")

    monkeypatch.setattr(db_session, "commit", commit_then_lose_acknowledgement)
    key = uuid4()
    payload = HomeworkCreate(student_id=student.id, text="Persist exactly once")
    created = await create_assignment(db_session, teacher, payload, key)
    assert created.material_count == 0

    retry = await create_assignment(db_session, teacher, payload, key)
    assert retry.id == created.id
    assert await db_session.scalar(select(func.count()).select_from(Homework)) == 1
