import asyncio
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
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.constants import (
    MAX_ASSIGNMENT_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_BYTES,
)
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.homework.schemas import HomeworkSubmissionCreate
from backend.homework.services.submissions import create_submission
from backend.main import app


@pytest.fixture
def homework_storage(tmp_path: Path) -> Generator[Path]:
    config = HomeworkConfig(STORAGE_PATH=tmp_path)
    app.dependency_overrides[get_homework_settings] = lambda: config
    try:
        yield tmp_path
    finally:
        app.dependency_overrides.pop(get_homework_settings, None)


async def _user(session: AsyncSession, role: str) -> User:
    user = User(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        role=role,
        timezone="UTC",
    )
    session.add(user)
    await session.commit()
    return user


async def _linked_pair(session: AsyncSession) -> tuple[User, User]:
    teacher = await _user(session, "TEACHER")
    student = await _user(session, "STUDENT")
    student.teacher_id = teacher.id
    await session.commit()
    return teacher, student


async def _assignment(
    session: AsyncSession,
    teacher: User,
    student: User,
) -> Homework:
    homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text="Complete the exercise",
        operation_key=uuid4(),
    )
    session.add(homework)
    await session.commit()
    return homework


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


async def _stage(
    client: AsyncClient,
    owner: User,
    *,
    name: str = "solution.txt",
    content: bytes = b"solution",
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/homework/attachments",
        headers={**_auth(owner), "Idempotency-Key": str(uuid4())},
        files={"file": (name, content)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_text_submission_completes_and_resubmissions_are_versioned(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    operation_key = uuid4()
    payload = {"text": "My answer", "operation_key": str(operation_key)}
    url = f"/api/v1/homework/assignments/{homework.id}/submissions"

    submitted = await client.post(url, headers=_auth(student), json=payload)
    assert submitted.status_code == 201, submitted.text
    assert submitted.json() == {
        "version": 1,
        "assignment_status": "COMPLETE",
        "completion_source": "STUDENT_SUBMISSION",
    }
    read_back = await client.get(
        f"/api/v1/homework/assignments/{homework.id}",
        headers=_auth(student),
    )
    assert read_back.status_code == 200
    assert read_back.json()["status"] == "COMPLETE"
    assert read_back.json()["completion_source"] == "STUDENT_SUBMISSION"
    assert read_back.json()["completed_at"] is not None

    retry = await client.post(url, headers=_auth(student), json=payload)
    assert retry.status_code == 201
    assert retry.json() == submitted.json()
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 1
    )

    reused_key = await client.post(
        url,
        headers=_auth(student),
        json={"text": "Different answer", "operation_key": str(operation_key)},
    )
    assert reused_key.status_code == 409

    homework_before_resubmission = await db_session.get(Homework, homework.id)
    assert homework_before_resubmission is not None
    completed_at = homework_before_resubmission.completed_at
    resubmission = await client.post(
        url,
        headers=_auth(student),
        json={"text": "Updated answer", "operation_key": str(uuid4())},
    )
    assert resubmission.status_code == 201, resubmission.text
    assert resubmission.json()["version"] == 2
    assert resubmission.json()["assignment_status"] == "COMPLETE"
    assert resubmission.json()["completion_source"] == "STUDENT_SUBMISSION"
    refreshed = await db_session.get(Homework, homework.id)
    assert refreshed is not None
    assert refreshed.completed_at == completed_at
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 2
    )


async def test_file_and_mixed_submissions_link_private_student_owned_files(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    url = f"/api/v1/homework/assignments/{homework.id}/submissions"

    staged_file = await _stage(client, student, content=b"file-only answer")
    submitted = await client.post(
        url,
        headers=_auth(student),
        json={
            "attachment_ids": [staged_file["id"]],
            "operation_key": str(uuid4()),
        },
    )
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["version"] == 1
    file_id = UUID(str(staged_file["id"]))
    attachment = await db_session.get(HomeworkAttachment, file_id)
    assert attachment is not None
    assert attachment.owner_user_id == student.id
    assert attachment.submission_id is not None
    assert attachment.homework_id == homework.id
    assert attachment.staged_until is None
    assert (
        await client.get(
            f"/api/v1/homework/attachments/{file_id}",
            headers=_auth(student),
        )
    ).content == b"file-only answer"
    assert (
        await client.get(
            f"/api/v1/homework/attachments/{file_id}",
            headers=_auth(teacher),
        )
    ).content == b"file-only answer"
    assert len(list(homework_storage.iterdir())) == 1

    mixed_file = await _stage(
        client,
        student,
        name="diagram.png",
        content=b"mixed answer file",
    )
    mixed = await client.post(
        url,
        headers=_auth(student),
        json={
            "text": "Here is my explanation",
            "attachment_ids": [mixed_file["id"]],
            "operation_key": str(uuid4()),
        },
    )
    assert mixed.status_code == 201, mixed.text
    assert mixed.json()["version"] == 2
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 2
    )


async def test_empty_and_unauthorized_submissions_are_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    url = f"/api/v1/homework/assignments/{homework.id}/submissions"

    for body in (
        {"operation_key": str(uuid4())},
        {"text": "  ", "operation_key": str(uuid4())},
        {
            "attachment_ids": [str(uuid4())] * 2,
            "operation_key": str(uuid4()),
        },
        {
            "text": "valid",
            "unexpected": True,
            "operation_key": str(uuid4()),
        },
    ):
        rejected = await client.post(url, headers=_auth(student), json=body)
        assert rejected.status_code == 422

    teacher_attempt = await client.post(
        url,
        headers=_auth(teacher),
        json={"text": "Forged", "operation_key": str(uuid4())},
    )
    assert teacher_attempt.status_code == 403

    unrelated_teacher, unrelated_student = await _linked_pair(db_session)
    teacher_rejected = await client.post(
        url,
        headers=_auth(unrelated_teacher),
        json={"text": "Forged", "operation_key": str(uuid4())},
    )
    assert teacher_rejected.status_code == 403
    student_rejected = await client.post(
        url,
        headers=_auth(unrelated_student),
        json={"text": "Forged", "operation_key": str(uuid4())},
    )
    assert student_rejected.status_code == 404
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 0
    )


async def test_unlinked_and_teacher_completed_homework_cannot_be_submitted(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    student.teacher_id = None
    await db_session.commit()
    url = f"/api/v1/homework/assignments/{homework.id}/submissions"
    unlinked = await client.post(
        url,
        headers=_auth(student),
        json={"text": "No longer linked", "operation_key": str(uuid4())},
    )
    assert unlinked.status_code == 404

    student.teacher_id = teacher.id
    homework.status = "COMPLETE"
    homework.completion_source = "TEACHER"
    homework.completed_at = datetime.now(UTC)
    await db_session.commit()
    blocked = await client.post(
        url,
        headers=_auth(student),
        json={"text": "Too late", "operation_key": str(uuid4())},
    )
    assert blocked.status_code == 409
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 0
    )
    homework.deleted_at = datetime.now(UTC)
    homework.cleanup_pending = True
    await db_session.commit()
    deleted_assignment = await client.post(
        url,
        headers=_auth(student),
        json={"text": "Deleted work", "operation_key": str(uuid4())},
    )
    assert deleted_assignment.status_code == 404


async def test_submission_attachment_limits_and_foreign_staging(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    _, other_student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    foreign = await _stage(client, other_student)
    url = f"/api/v1/homework/assignments/{homework.id}/submissions"

    forged = await client.post(
        url,
        headers=_auth(student),
        json={
            "attachment_ids": [foreign["id"]],
            "operation_key": str(uuid4()),
        },
    )
    assert forged.status_code == 404

    sizes = [
        MAX_ATTACHMENT_BYTES,
        MAX_ATTACHMENT_BYTES,
        MAX_ASSIGNMENT_ATTACHMENT_BYTES - 2 * MAX_ATTACHMENT_BYTES - 2,
        1,
        1,
    ]
    staged = []
    for size in sizes:
        attachment = HomeworkAttachment(
            owner_user_id=student.id,
            storage_key=uuid4().hex,
            file_name="staged.bin",
            byte_size=size,
            staged_until=datetime.now(UTC) + timedelta(hours=1),
        )
        db_session.add(attachment)
        staged.append(attachment)
    expired = HomeworkAttachment(
        owner_user_id=student.id,
        storage_key=uuid4().hex,
        file_name="expired.bin",
        byte_size=1,
        staged_until=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(expired)
    await db_session.commit()

    exact_limit = await client.post(
        url,
        headers=_auth(student),
        json={
            "attachment_ids": [str(item.id) for item in staged],
            "operation_key": str(uuid4()),
        },
    )
    assert exact_limit.status_code == 201, exact_limit.text

    too_many = await client.post(
        f"/api/v1/homework/assignments/{(await _assignment(db_session, teacher, student)).id}/submissions",
        headers=_auth(student),
        json={
            "attachment_ids": [str(uuid4()) for _ in range(6)],
            "operation_key": str(uuid4()),
        },
    )
    assert too_many.status_code == 422

    expired_attempt = await client.post(
        f"/api/v1/homework/assignments/{(await _assignment(db_session, teacher, student)).id}/submissions",
        headers=_auth(student),
        json={
            "attachment_ids": [str(expired.id)],
            "operation_key": str(uuid4()),
        },
    )
    assert expired_attempt.status_code == 410
    oversized_assignment = await _assignment(db_session, teacher, student)
    oversized_sizes = [
        MAX_ATTACHMENT_BYTES,
        MAX_ATTACHMENT_BYTES,
        MAX_ASSIGNMENT_ATTACHMENT_BYTES - 2 * MAX_ATTACHMENT_BYTES + 1,
    ]
    oversized = []
    for size in oversized_sizes:
        attachment = HomeworkAttachment(
            owner_user_id=student.id,
            storage_key=uuid4().hex,
            file_name="oversized.bin",
            byte_size=size,
            staged_until=datetime.now(UTC) + timedelta(hours=1),
        )
        db_session.add(attachment)
        oversized.append(attachment)
    await db_session.commit()
    over_limit = await client.post(
        f"/api/v1/homework/assignments/{oversized_assignment.id}/submissions",
        headers=_auth(student),
        json={
            "attachment_ids": [str(item.id) for item in oversized],
            "operation_key": str(uuid4()),
        },
    )
    assert over_limit.status_code == 413
    assert (
        over_limit.json()["detail"] == "Submission attachments exceed the 50 MiB limit"
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 1
    )


async def test_failed_finalization_preserves_staged_files_and_unknown_ack_reconciles(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    attachment = HomeworkAttachment(
        owner_user_id=student.id,
        storage_key=uuid4().hex,
        file_name="staged.txt",
        byte_size=4,
        staged_until=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(attachment)
    await db_session.commit()
    attachment_id = attachment.id
    student_id = student.id
    homework_id = homework.id
    payload = HomeworkSubmissionCreate(
        attachment_ids=[attachment_id],
        operation_key=uuid4(),
    )
    original_commit = db_session.commit

    async def fail_commit() -> None:
        raise SQLAlchemyError("simulated database failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(HTTPException) as raised:
        await create_submission(db_session, student, homework.id, payload)
    assert raised.value.status_code == 503
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 0
    )
    unchanged = await db_session.get(HomeworkAttachment, attachment_id)
    assert unchanged is not None
    assert unchanged.submission_id is None
    assert unchanged.staged_until is not None
    incomplete = await db_session.get(Homework, homework.id)
    assert incomplete is not None
    assert incomplete.status == "INCOMPLETE"

    student = await db_session.get(User, student_id)
    assert student is not None

    async def commit_then_lose_acknowledgement() -> None:
        await original_commit()
        raise SQLAlchemyError("simulated lost commit acknowledgement")

    monkeypatch.setattr(db_session, "commit", commit_then_lose_acknowledgement)
    result = await create_submission(db_session, student, homework_id, payload)
    assert result.version == 1
    assert result.assignment_status == "COMPLETE"
    assert result.completion_source == "STUDENT_SUBMISSION"
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 1
    )


async def test_concurrent_retries_with_one_operation_key_create_one_version(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    url = f"/api/v1/homework/assignments/{homework.id}/submissions"
    body = {"text": "One logical submission", "operation_key": str(uuid4())}

    results = await asyncio.gather(
        client.post(url, headers=_auth(student), json=body),
        client.post(url, headers=_auth(student), json=body),
    )
    accepted = [result for result in results if result.status_code == 201]
    assert accepted
    assert all(result.status_code in {201, 409} for result in results)
    for result in results:
        if result.status_code == 409:
            retried = await client.post(url, headers=_auth(student), json=body)
            assert retried.status_code == 201, retried.text
            assert retried.json() == accepted[0].json()
    if len(accepted) == 2:
        assert accepted[0].json() == accepted[1].json()
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 1
    )
