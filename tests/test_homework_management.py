import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import backend.homework.management_service as management_service
from backend.auth.models import User
from backend.auth.utils import create_access_token
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
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


async def test_teacher_reviews_versioned_submissions_and_materials(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    url = f"/api/v1/homework/assignments/{homework.id}/submissions"
    first = await client.post(
        url,
        headers=_auth(student),
        json={"text": "First answer", "operation_key": str(uuid4())},
    )
    assert first.status_code == 201
    staged = await client.post(
        "/api/v1/homework/attachments",
        headers={**_auth(student), "Idempotency-Key": str(uuid4())},
        files={"file": ("answer.pdf", b"second answer")},
    )
    assert staged.status_code == 201
    second = await client.post(
        url,
        headers=_auth(student),
        json={
            "attachment_ids": [staged.json()["id"]],
            "operation_key": str(uuid4()),
        },
    )
    assert second.status_code == 201

    page = await client.get(
        f"{url}?skip=0&limit=1",
        headers=_auth(teacher),
    )
    assert page.status_code == 200
    assert page.json()["homework_id"] == str(homework.id)
    assert page.json()["total"] == 2
    assert page.json()["items"][0]["version"] == 2
    assert page.json()["items"][0]["is_current"] is True
    assert page.json()["items"][0]["text"] is None
    assert page.json()["items"][0]["materials"] == [
        {
            "id": staged.json()["id"],
            "file_name": "answer.pdf",
            "byte_size": len(b"second answer"),
        }
    ]
    assert "storage_key" not in page.text
    previous = await client.get(
        f"{url}?skip=1&limit=1",
        headers=_auth(teacher),
    )
    assert previous.status_code == 200
    assert previous.json()["items"][0]["version"] == 1
    assert previous.json()["items"][0]["is_current"] is False

    denied = await client.get(url, headers=_auth(student))
    assert denied.status_code == 403
    other_teacher, _ = await _linked_pair(db_session)
    hidden = await client.get(url, headers=_auth(other_teacher))
    assert hidden.status_code == 404


async def test_manual_completion_is_idempotent_and_preserves_provenance(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    url = f"/api/v1/homework/assignments/{homework.id}/complete"

    completed = await client.post(url, headers=_auth(teacher))
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETE"
    assert completed.json()["completion_source"] == "TEACHER"
    assert completed.json()["completed_at"] is not None
    repeated = await client.post(url, headers=_auth(teacher))
    assert repeated.status_code == 200
    assert repeated.json() == completed.json()
    await db_session.refresh(homework)
    assert homework.completion_source == "TEACHER"

    student_submission = await client.post(
        f"/api/v1/homework/assignments/{homework.id}/submissions",
        headers=_auth(student),
        json={"text": "Too late", "operation_key": str(uuid4())},
    )
    assert student_submission.status_code == 409
    forged = await client.post(url, headers=_auth(student))
    assert forged.status_code == 403

    teacher2, student2 = await _linked_pair(db_session)
    unrelated = await client.post(
        url,
        headers=_auth(teacher2),
    )
    assert unrelated.status_code == 404

    student_completed_homework = await _assignment(db_session, teacher2, student2)
    submission = await client.post(
        f"/api/v1/homework/assignments/{student_completed_homework.id}/submissions",
        headers=_auth(student2),
        json={"text": "Completed by student", "operation_key": str(uuid4())},
    )
    assert submission.status_code == 201
    student_complete_attempt = await client.post(
        f"/api/v1/homework/assignments/{student_completed_homework.id}/complete",
        headers=_auth(teacher2),
    )
    assert student_complete_attempt.status_code == 409
    stored = await db_session.get(Homework, student_completed_homework.id)
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.completion_source == "STUDENT_SUBMISSION"


async def test_manual_completion_and_submission_race_has_one_winner(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    completion_url = f"/api/v1/homework/assignments/{homework.id}/complete"
    submission_url = f"/api/v1/homework/assignments/{homework.id}/submissions"

    completion, submission = await asyncio.gather(
        client.post(completion_url, headers=_auth(teacher)),
        client.post(
            submission_url,
            headers=_auth(student),
            json={"text": "My answer", "operation_key": str(uuid4())},
        ),
    )
    assert (completion.status_code, submission.status_code) in {
        (200, 409),
        (409, 201),
    }
    stored = await db_session.get(Homework, homework.id)
    assert stored is not None
    await db_session.refresh(stored)
    if submission.status_code == 201:
        assert stored.completion_source == "STUDENT_SUBMISSION"
        assert (
            await db_session.scalar(
                select(func.count()).select_from(HomeworkSubmission)
            )
            == 1
        )
    else:
        assert stored.completion_source == "TEACHER"
        assert (
            await db_session.scalar(
                select(func.count()).select_from(HomeworkSubmission)
            )
            == 0
        )


async def test_delete_and_submission_race_never_leaves_assignment_or_submission(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    delete_url = f"/api/v1/homework/assignments/{homework.id}"
    submission_url = f"{delete_url}/submissions"

    deleted, submission = await asyncio.gather(
        client.delete(delete_url, headers=_auth(teacher)),
        client.post(
            submission_url,
            headers=_auth(student),
            json={"text": "My answer", "operation_key": str(uuid4())},
        ),
    )
    assert deleted.status_code == 204
    assert submission.status_code in {201, 404}
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Homework).where(Homework.id == homework.id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(HomeworkSubmission)
            .where(HomeworkSubmission.homework_id == homework.id)
        )
        == 0
    )


async def test_concurrent_delete_requests_are_safe(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    url = f"/api/v1/homework/assignments/{homework.id}"
    attachment = HomeworkAttachment(
        owner_user_id=teacher.id,
        homework_id=homework.id,
        storage_key=uuid4().hex,
        file_name="material.pdf",
        byte_size=4,
        staged_until=None,
    )
    path = homework_storage / attachment.storage_key
    path.write_bytes(b"file")
    db_session.add(attachment)
    await db_session.commit()

    responses = await asyncio.gather(
        client.delete(url, headers=_auth(teacher)),
        client.delete(url, headers=_auth(teacher)),
    )
    assert all(response.status_code in {204, 404} for response in responses)
    assert any(response.status_code == 204 for response in responses)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Homework).where(Homework.id == homework.id)
        )
        == 0
    )
    assert not path.exists()
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(HomeworkAttachment)
            .where(HomeworkAttachment.homework_id == homework.id)
        )
        == 0
    )


async def test_delete_removes_submission_files_and_revokes_access(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    submission = HomeworkSubmission(
        homework_id=homework.id,
        student_id=student.id,
        version=1,
        text="My answer",
        operation_key=uuid4(),
    )
    homework.status = "COMPLETE"
    homework.completion_source = "STUDENT_SUBMISSION"
    homework.completed_at = datetime.now(UTC)
    db_session.add(submission)
    await db_session.flush()

    materials = []
    for owner, submission_id, name, content in (
        (teacher, None, "material.pdf", b"assignment material"),
        (student, submission.id, "answer.pdf", b"student solution"),
    ):
        attachment = HomeworkAttachment(
            owner_user_id=owner.id,
            homework_id=homework.id,
            submission_id=submission_id,
            storage_key=uuid4().hex,
            file_name=name,
            byte_size=len(content),
            staged_until=None,
        )
        (homework_storage / attachment.storage_key).write_bytes(content)
        db_session.add(attachment)
        materials.append(attachment)
    await db_session.commit()
    homework_id = homework.id
    attachment_ids = [item.id for item in materials]

    deleted = await client.delete(
        f"/api/v1/homework/assignments/{homework_id}",
        headers=_auth(teacher),
    )
    assert deleted.status_code == 204
    assert all(not (homework_storage / item.storage_key).exists() for item in materials)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Homework).where(Homework.id == homework_id)
        )
        == 0
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 0
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkAttachment))
        == 0
    )
    assert (
        await client.get(
            f"/api/v1/homework/assignments/{homework_id}",
            headers=_auth(teacher),
        )
    ).status_code == 404
    for attachment_id in attachment_ids:
        assert (
            await client.get(
                f"/api/v1/homework/attachments/{attachment_id}",
                headers=_auth(teacher),
            )
        ).status_code == 404
    repeated = await client.delete(
        f"/api/v1/homework/assignments/{homework_id}",
        headers=_auth(teacher),
    )
    assert repeated.status_code == 404


async def test_failed_delete_cleanup_keeps_tombstone_and_retries(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    storage_key = uuid4().hex
    attachment = HomeworkAttachment(
        owner_user_id=teacher.id,
        homework_id=homework.id,
        storage_key=storage_key,
        file_name="material.pdf",
        byte_size=4,
        staged_until=None,
    )
    path = homework_storage / storage_key
    path.write_bytes(b"file")
    db_session.add(attachment)
    await db_session.commit()

    async def fail_cleanup(
        _attachments: list[HomeworkAttachment],
        _config: HomeworkConfig,
    ) -> None:
        raise HTTPException(status_code=503, detail="simulated storage failure")

    original_cleanup = (
        management_service.attachment_service.remove_committed_attachment_files
    )
    monkeypatch.setattr(
        management_service.attachment_service,
        "remove_committed_attachment_files",
        fail_cleanup,
    )
    url = f"/api/v1/homework/assignments/{homework.id}"
    failed = await client.delete(url, headers=_auth(teacher))
    assert failed.status_code == 503
    assert path.exists()
    tombstone = await db_session.get(Homework, homework.id)
    assert tombstone is not None
    await db_session.refresh(tombstone)
    assert tombstone.deleted_at is not None
    assert tombstone.cleanup_pending is True
    assert (await client.get(url, headers=_auth(teacher))).status_code == 404
    assert (
        await client.get(
            f"/api/v1/homework/attachments/{attachment.id}",
            headers=_auth(teacher),
        )
    ).status_code == 404

    monkeypatch.setattr(
        management_service.attachment_service,
        "remove_committed_attachment_files",
        original_cleanup,
    )
    retried = await client.delete(url, headers=_auth(teacher))
    assert retried.status_code == 204
    assert not path.exists()
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Homework).where(Homework.id == homework.id)
        )
        == 0
    )


async def test_unlinked_teacher_cannot_manage_homework(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher, student = await _linked_pair(db_session)
    homework = await _assignment(db_session, teacher, student)
    student.teacher_id = None
    await db_session.commit()

    for method, route in (
        ("get", f"/api/v1/homework/assignments/{homework.id}/submissions"),
        ("post", f"/api/v1/homework/assignments/{homework.id}/complete"),
        ("delete", f"/api/v1/homework/assignments/{homework.id}"),
    ):
        response = await getattr(client, method)(route, headers=_auth(teacher))
        assert response.status_code == 404
