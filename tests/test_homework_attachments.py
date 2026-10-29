import asyncio
import os
import stat
from collections.abc import AsyncIterator, Generator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

import backend.homework.router as homework_router_module
import backend.homework.services.attachments as homework_service_module
from backend.auth.models import User
from backend.auth.utils import create_access_token
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.constants import STAGED_ATTACHMENT_TTL
from backend.homework.models import Homework, HomeworkAttachment
from backend.homework.services.attachments import (
    cleanup_expired_staged_attachments,
    stage_attachment,
    stream_attachment,
)
from backend.main import app


@pytest.fixture
def homework_storage(tmp_path) -> Generator[Path]:
    config = HomeworkConfig(STORAGE_PATH=tmp_path)
    app.dependency_overrides[get_homework_settings] = lambda: config
    try:
        yield tmp_path
    finally:
        app.dependency_overrides.pop(get_homework_settings, None)


async def _user(session: AsyncSession, role: str = "TEACHER") -> User:
    user = User(
        id=uuid4(),
        email=f"homework-{uuid4()}@example.com",
        role=role,
        timezone="UTC",
    )
    session.add(user)
    await session.commit()
    return user


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def test_staged_attachment_upload_download_authorization_and_cancel(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    owner = await _user(db_session)
    outsider = await _user(db_session, "STUDENT")
    operation_key = uuid4()
    headers = {**_auth(owner), "Idempotency-Key": str(operation_key)}

    uploaded = await client.post(
        "/api/v1/homework/attachments",
        headers=headers,
        files={"file": ("../../../../etc/passwd", b"hello", "text/plain")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["id"] == str(operation_key)
    assert uploaded.json()["file_name"] == "passwd"
    assert uploaded.json()["byte_size"] == 5
    expiry = datetime.fromisoformat(uploaded.json()["expires_at"])
    assert expiry > datetime.now(UTC)
    stored_files = list(homework_storage.iterdir())
    assert len(stored_files) == 1
    assert stored_files[0].name != "passwd"
    assert stored_files[0].read_bytes() == b"hello"
    assert stat.S_IMODE(homework_storage.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored_files[0].stat().st_mode) == 0o600

    duplicate = await client.post(
        "/api/v1/homework/attachments",
        headers=headers,
        files={"file": ("replacement.txt", b"new bytes", "text/plain")},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == uploaded.json()["id"]
    assert duplicate.json()["byte_size"] == 5

    download = await client.get(
        f"/api/v1/homework/attachments/{operation_key}", headers=_auth(owner)
    )
    assert download.status_code == 200
    assert download.content == b"hello"
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''passwd"
    )
    assert (
        await client.get(f"/api/v1/homework/attachments/{operation_key}")
    ).status_code == 401
    assert (
        await client.get(
            f"/api/v1/homework/attachments/{operation_key}",
            headers=_auth(outsider),
        )
    ).status_code == 404

    deleted = await client.delete(
        f"/api/v1/homework/attachments/{operation_key}", headers=_auth(owner)
    )
    assert deleted.status_code == 204
    assert not stored_files[0].exists()
    assert (
        await client.get(
            f"/api/v1/homework/attachments/{operation_key}",
            headers=_auth(owner),
        )
    ).status_code == 404


async def test_committed_attachment_survives_storage_configuration_recreation(
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    teacher = await _user(db_session)
    student = await _user(db_session, "STUDENT")
    student.teacher_id = teacher.id
    homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text="Read the material",
        operation_key=uuid4(),
    )
    db_session.add(homework)
    await db_session.flush()
    storage_key = uuid4().hex
    content = b"persisted assignment material"
    stored_file = homework_storage / storage_key
    stored_file.write_bytes(content)
    attachment = HomeworkAttachment(
        owner_user_id=teacher.id,
        homework_id=homework.id,
        storage_key=storage_key,
        file_name="material.pdf",
        byte_size=len(content),
        staged_until=None,
    )
    db_session.add(attachment)
    await db_session.commit()

    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=homework_storage
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as restarted_client:
        download = await restarted_client.get(
            f"/api/v1/homework/attachments/{attachment.id}",
            headers=_auth(teacher),
        )
    assert download.status_code == 200
    assert download.content == content
    assert stored_file.exists()


async def test_chunked_oversized_upload_is_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session)

    async def oversized_body() -> AsyncIterator[bytes]:
        yield (
            b"--oversized\r\n"
            b'Content-Disposition: form-data; name="file"; filename="large.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
        )
        chunk = b"x" * (1024 * 1024)
        for _ in range(21):
            yield chunk
        yield b"\r\n--oversized--\r\n"

    response = await client.post(
        "/api/v1/homework/attachments",
        content=oversized_body(),
        headers={
            **_auth(owner),
            "Idempotency-Key": str(uuid4()),
            "Content-Type": "multipart/form-data; boundary=oversized",
        },
    )
    assert response.status_code == 413, response.text


async def test_upload_transfer_timeout_returns_request_timeout(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _user(db_session)
    monkeypatch.setattr(
        homework_router_module, "ATTACHMENT_TRANSFER_TIMEOUT_SECONDS", 0.01
    )

    async def slow_body() -> AsyncIterator[bytes]:
        yield (
            b"--slow\r\n"
            b'Content-Disposition: form-data; name="file"; filename="slow.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
        )
        await asyncio.sleep(0.02)
        yield b"content\r\n--slow--\r\n"

    response = await client.post(
        "/api/v1/homework/attachments",
        content=slow_body(),
        headers={
            **_auth(owner),
            "Idempotency-Key": str(uuid4()),
            "Content-Type": "multipart/form-data; boundary=slow",
        },
    )
    assert response.status_code == 408, response.text
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkAttachment))
        == 0
    )
    assert list(homework_storage.iterdir()) == []


async def test_multiple_files_in_one_upload_are_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    owner = await _user(db_session)
    response = await client.post(
        "/api/v1/homework/attachments",
        headers={**_auth(owner), "Idempotency-Key": str(uuid4())},
        files=[
            ("file", ("first.txt", b"one")),
            ("file", ("second.txt", b"two")),
        ],
    )
    assert response.status_code == 400
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkAttachment))
        == 0
    )
    assert list(homework_storage.iterdir()) == []


async def test_oversized_file_and_storage_failure_leave_no_metadata(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    owner = await _user(db_session)
    too_large = await client.post(
        "/api/v1/homework/attachments",
        headers={**_auth(owner), "Idempotency-Key": str(uuid4())},
        files={"file": ("large.bin", b"x" * (20 * 1024 * 1024 + 1))},
    )
    assert too_large.status_code == 413
    assert list(homework_storage.iterdir()) == []
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkAttachment))
        == 0
    )

    blocked_root = homework_storage / "not-a-directory"
    blocked_root.write_text("block")
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=blocked_root
    )
    storage_failure = await client.post(
        "/api/v1/homework/attachments",
        headers={**_auth(owner), "Idempotency-Key": str(uuid4())},
        files={"file": ("small.txt", b"data")},
    )
    assert storage_failure.status_code == 503
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkAttachment))
        == 0
    )


class _DisconnectingUpload(UploadFile):
    async def read(self, size: int = -1) -> bytes:
        raise ClientDisconnect()


class _SlowReadable:
    closed = False

    async def read(self, size: int = -1) -> bytes:
        await asyncio.sleep(0.02)
        return b"content"

    async def close(self) -> None:
        self.closed = True


async def test_download_transfer_timeout_closes_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readable = _SlowReadable()
    monkeypatch.setattr(
        homework_service_module, "ATTACHMENT_TRANSFER_TIMEOUT_SECONDS", 0.01
    )

    with pytest.raises(TimeoutError):
        async for _ in stream_attachment(readable):
            pass

    assert readable.closed


async def test_disconnected_upload_removes_temporary_state(
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    owner = await _user(db_session)
    upload = _DisconnectingUpload(filename="partial.bin", file=BytesIO())
    with pytest.raises(ClientDisconnect):
        await stage_attachment(
            db_session,
            owner,
            upload,
            uuid4(),
            HomeworkConfig(STORAGE_PATH=homework_storage),
        )

    assert list(homework_storage.iterdir()) == []
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkAttachment))
        == 0
    )


async def test_retry_reclaims_a_stale_upload_lock(
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    owner = await _user(db_session)
    operation_key = uuid4()
    stale_lock = homework_storage / f".upload-{operation_key.hex}.lock"
    stale_lock.touch()
    os.utime(stale_lock, (0, 0))

    attachment = await stage_attachment(
        db_session,
        owner,
        UploadFile(filename="retried.txt", file=BytesIO(b"content")),
        operation_key,
        HomeworkConfig(STORAGE_PATH=homework_storage),
    )

    assert attachment.byte_size == len(b"content")
    assert not stale_lock.exists()
    assert len(list(homework_storage.iterdir())) == 1


async def test_daily_cleanup_removes_expired_staged_files(
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    owner = await _user(db_session)
    storage_key = uuid4().hex
    stored_file = homework_storage / storage_key
    stored_file.write_bytes(b"expired")
    attachment = HomeworkAttachment(
        owner_user_id=owner.id,
        storage_key=storage_key,
        file_name="expired.txt",
        byte_size=7,
        staged_until=datetime.now(UTC) - STAGED_ATTACHMENT_TTL - timedelta(seconds=1),
    )
    db_session.add(attachment)
    await db_session.commit()

    summary = await cleanup_expired_staged_attachments(
        db_session, HomeworkConfig(STORAGE_PATH=homework_storage)
    )
    assert summary.deleted_staged == 1
    assert summary.failures == 0
    assert not stored_file.exists()
    assert await db_session.get(HomeworkAttachment, attachment.id) is None
