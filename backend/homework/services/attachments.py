import asyncio
import logging
import os
import re
import stat
import unicodedata
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import aiofiles
import aiofiles.os
from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.homework.config import HomeworkConfig
from backend.homework.constants import (
    ATTACHMENT_TRANSFER_TIMEOUT_SECONDS,
    MAX_ATTACHMENT_BYTES,
    STAGED_ATTACHMENT_TTL,
    UPLOAD_CHUNK_BYTES,
    UPLOAD_LOCK_STALE_AFTER_SECONDS,
)
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission

logger = logging.getLogger(__name__)
_STORAGE_KEY = re.compile(r"[0-9a-f]{32}\Z")


class _UploadTooLarge(Exception):
    pass


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class AttachmentDownload:
    file_name: str
    byte_size: int
    file: AsyncReadable

    @property
    def content_disposition(self) -> str:
        return f"attachment; filename*=UTF-8''{quote(self.file_name, safe='')}"


@dataclass(frozen=True)
class CleanupSummary:
    deleted_staged: int
    deleted_orphans: int
    failures: int


def sanitize_filename(filename: str | None) -> str:
    name = (filename or "attachment").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        character
        for character in name
        if unicodedata.category(character)[0] != "C" and character not in "/\\"
    )
    name = name.strip()[:128]
    return name or "attachment"


async def _storage_root(config: HomeworkConfig) -> Path:
    root = await asyncio.to_thread(config.STORAGE_PATH.expanduser().resolve)
    await aiofiles.os.makedirs(root, mode=0o700, exist_ok=True)
    await asyncio.to_thread(os.chmod, root, 0o700)
    return root


async def _available_storage_root(config: HomeworkConfig) -> Path:
    try:
        return await _storage_root(config)
    except OSError as exc:
        logger.exception("Private homework storage is unavailable")
        raise HTTPException(
            status_code=503, detail="Homework storage is unavailable"
        ) from exc


async def _acquire_upload_lock(lock_path: Path) -> None:
    try:
        async with aiofiles.open(lock_path, "xb"):
            return
    except FileExistsError as first_error:
        try:
            try:
                lock_stat = await aiofiles.os.stat(lock_path, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                lock_age = datetime.now(UTC).timestamp() - lock_stat.st_mtime
                if lock_age < UPLOAD_LOCK_STALE_AFTER_SECONDS:
                    raise HTTPException(
                        status_code=409,
                        detail="Attachment upload is already in progress",
                    ) from first_error
                await aiofiles.os.remove(lock_path)

            async with aiofiles.open(lock_path, "xb"):
                return
        except FileExistsError as exc:
            raise HTTPException(
                status_code=409, detail="Attachment upload is already in progress"
            ) from exc
        except HTTPException:
            raise
        except OSError as exc:
            logger.exception("Unable to recover a homework upload lock")
            raise HTTPException(
                status_code=503, detail="Homework storage is unavailable"
            ) from exc
    except OSError as exc:
        logger.exception("Unable to stage a homework attachment")
        raise HTTPException(
            status_code=503, detail="Homework storage is unavailable"
        ) from exc


def _storage_path(root: Path, storage_key: str) -> Path:
    if _STORAGE_KEY.fullmatch(storage_key) is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment storage metadata is invalid",
        )
    return root / storage_key


async def _remove_paths(paths: tuple[Path, ...]) -> bool:
    succeeded = True
    for path in paths:
        try:
            await aiofiles.os.remove(path)
        except FileNotFoundError:
            continue
        except OSError:
            logger.exception("Failed to remove temporary homework storage file")
            succeeded = False
    return succeeded


async def remove_committed_attachment_files(
    attachments: Sequence[HomeworkAttachment],
    config: HomeworkConfig,
) -> None:
    if not attachments:
        return
    root = await _available_storage_root(config)
    paths = tuple(
        _storage_path(root, attachment.storage_key) for attachment in attachments
    )
    if not await _remove_paths(paths):
        raise HTTPException(
            status_code=503,
            detail="Homework is hidden; file cleanup can be retried",
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _attachment_expired(attachment: HomeworkAttachment) -> bool:
    return attachment.staged_until is not None and _as_utc(
        attachment.staged_until
    ) <= datetime.now(UTC)


async def stage_attachment(
    session: AsyncSession,
    owner: User,
    upload: UploadFile,
    operation_key: uuid.UUID,
    config: HomeworkConfig,
) -> HomeworkAttachment:
    existing = await session.get(HomeworkAttachment, operation_key)
    if existing is not None:
        if existing.owner_user_id != owner.id:
            raise HTTPException(status_code=404, detail="Attachment not found")
        if existing.cleanup_pending or existing.homework_id is not None:
            raise HTTPException(
                status_code=409, detail="Attachment operation is already finalized"
            )
        if _attachment_expired(existing):
            raise HTTPException(status_code=410, detail="Staged attachment has expired")
        return existing

    root = await _available_storage_root(config)

    lock_path = root / f".upload-{operation_key.hex}.lock"
    temporary_path = root / f".upload-{operation_key.hex}.part"
    await _acquire_upload_lock(lock_path)

    storage_key = uuid.uuid4().hex
    final_path = _storage_path(root, storage_key)
    byte_size = 0
    try:
        try:
            async with aiofiles.open(temporary_path, "xb") as output:
                await asyncio.to_thread(os.chmod, temporary_path, 0o600)
                while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
                    byte_size += len(chunk)
                    if byte_size > MAX_ATTACHMENT_BYTES:
                        raise _UploadTooLarge()
                    await output.write(chunk)
            await aiofiles.os.replace(temporary_path, final_path)
        except _UploadTooLarge as exc:
            cleaned = await _remove_paths((temporary_path, final_path))
            if not cleaned:
                raise HTTPException(
                    status_code=503,
                    detail="Oversized upload could not be fully cleaned up",
                ) from exc
            raise HTTPException(
                status_code=413, detail="Attachment exceeds the 20 MiB limit"
            ) from exc
        except ClientDisconnect:
            await _remove_paths((temporary_path, final_path))
            raise
        except asyncio.CancelledError:
            await _remove_paths((temporary_path, final_path))
            raise
        except OSError as exc:
            await _remove_paths((temporary_path, final_path))
            logger.exception("Unable to write a homework attachment")
            raise HTTPException(
                status_code=503, detail="Homework storage is unavailable"
            ) from exc

        attachment = HomeworkAttachment(
            id=operation_key,
            owner_user_id=owner.id,
            storage_key=storage_key,
            file_name=sanitize_filename(upload.filename),
            byte_size=byte_size,
            staged_until=datetime.now(UTC) + STAGED_ATTACHMENT_TTL,
        )
        session.add(attachment)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            existing = await session.get(HomeworkAttachment, operation_key)
            if not await _remove_paths((final_path,)):
                raise HTTPException(
                    status_code=503,
                    detail="Attachment upload could not be reconciled",
                ) from exc
            if existing is not None and existing.owner_user_id == owner.id:
                return existing
            if existing is not None:
                raise HTTPException(
                    status_code=404, detail="Attachment not found"
                ) from exc
            raise HTTPException(
                status_code=409, detail="Attachment upload could not be staged"
            ) from exc
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.exception("Unable to persist homework attachment metadata")
            raise HTTPException(
                status_code=503,
                detail="Attachment upload outcome is unknown; retry with the same key",
            ) from exc
        return attachment
    finally:
        if not await _remove_paths((lock_path,)):
            logger.error("Stale homework upload lock will be removed by cleanup")


async def _mark_cleanup_pending(
    session: AsyncSession, attachment: HomeworkAttachment
) -> None:
    if not attachment.cleanup_pending:
        attachment.cleanup_pending = True
        try:
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.exception("Unable to persist homework attachment cleanup state")
            raise HTTPException(
                status_code=503, detail="Attachment cleanup could not be recorded"
            ) from exc


async def _purge_staged_attachment(
    session: AsyncSession, attachment: HomeworkAttachment, root: Path
) -> None:
    if attachment.homework_id is not None or attachment.submission_id is not None:
        raise HTTPException(status_code=409, detail="Attachment is already assigned")

    await _mark_cleanup_pending(session, attachment)
    path = _storage_path(root, attachment.storage_key)
    if not await _remove_paths((path,)):
        raise HTTPException(
            status_code=503,
            detail="Attachment access is revoked; file cleanup can be retried",
        )

    await session.delete(attachment)
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("Unable to remove staged attachment metadata")
        raise HTTPException(
            status_code=503,
            detail="Attachment file was removed; metadata cleanup can be retried",
        ) from exc


async def cancel_staged_attachment(
    session: AsyncSession,
    owner: User,
    attachment_id: uuid.UUID,
    config: HomeworkConfig,
) -> None:
    attachment = await session.get(HomeworkAttachment, attachment_id)
    if attachment is None or attachment.owner_user_id != owner.id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if attachment.cleanup_pending or attachment.homework_id is not None:
        if attachment.cleanup_pending and attachment.homework_id is None:
            await _purge_staged_attachment(
                session, attachment, await _available_storage_root(config)
            )
            return
        raise HTTPException(status_code=409, detail="Attachment is already assigned")

    await _purge_staged_attachment(
        session, attachment, await _available_storage_root(config)
    )


async def open_attachment_download(
    session: AsyncSession,
    viewer: User,
    attachment_id: uuid.UUID,
    config: HomeworkConfig,
) -> AttachmentDownload:
    attachment = await session.get(HomeworkAttachment, attachment_id)
    if attachment is None or attachment.cleanup_pending:
        raise HTTPException(status_code=404, detail="Attachment not found")

    if attachment.homework_id is None:
        if (
            attachment.owner_user_id != viewer.id
            or _attachment_expired(attachment)
            or attachment.staged_until is None
        ):
            raise HTTPException(status_code=404, detail="Attachment not found")
    else:
        homework = await session.get(Homework, attachment.homework_id)
        if (
            homework is None
            or homework.deleted_at is not None
            or homework.cleanup_pending
            or viewer.id not in {homework.teacher_id, homework.student_id}
        ):
            raise HTTPException(status_code=404, detail="Attachment not found")
        student = await session.get(User, homework.student_id)
        teacher = await session.get(User, homework.teacher_id)
        if (
            student is None
            or student.role != Roles.STUDENT
            or not student.is_active
            or student.teacher_id != homework.teacher_id
            or teacher is None
            or teacher.role != Roles.TEACHER
            or not teacher.is_active
        ):
            raise HTTPException(status_code=404, detail="Attachment not found")
        expected_owner = (
            homework.student_id
            if attachment.submission_id is not None
            else homework.teacher_id
        )
        if attachment.owner_user_id != expected_owner:
            raise HTTPException(status_code=404, detail="Attachment not found")
        if attachment.submission_id is not None:
            submission = await session.get(HomeworkSubmission, attachment.submission_id)
            if (
                submission is None
                or submission.homework_id != homework.id
                or submission.student_id != homework.student_id
            ):
                raise HTTPException(status_code=404, detail="Attachment not found")

    root = await _available_storage_root(config)
    path = _storage_path(root, attachment.storage_key)
    try:
        file = await aiofiles.open(path, "rb")
    except OSError as exc:
        logger.exception("Unable to open a homework attachment")
        raise HTTPException(
            status_code=503, detail="Attachment storage is unavailable"
        ) from exc
    return AttachmentDownload(attachment.file_name, attachment.byte_size, file)


async def stream_attachment(file: AsyncReadable) -> AsyncIterator[bytes]:
    try:
        async with asyncio.timeout(ATTACHMENT_TRANSFER_TIMEOUT_SECONDS):
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                yield chunk
    except OSError:
        logger.exception("Homework attachment download failed while streaming")
        raise
    except TimeoutError:
        logger.warning("Homework attachment download exceeded the transfer timeout")
        raise
    finally:
        await file.close()


async def cleanup_expired_staged_attachments(
    session: AsyncSession, config: HomeworkConfig
) -> CleanupSummary:
    try:
        root = await _storage_root(config)
        expired_ids = list(
            await session.scalars(
                select(HomeworkAttachment.id).where(
                    HomeworkAttachment.homework_id.is_(None),
                    HomeworkAttachment.submission_id.is_(None),
                    or_(
                        HomeworkAttachment.cleanup_pending.is_(True),
                        HomeworkAttachment.staged_until <= datetime.now(UTC),
                    ),
                )
            )
        )
    except (OSError, SQLAlchemyError) as exc:
        logger.exception("Unable to enumerate staged homework attachments")
        raise HTTPException(
            status_code=503, detail="Homework cleanup could not be started"
        ) from exc

    deleted_staged = 0
    failures = 0
    for attachment_id in expired_ids:
        attachment = await session.get(HomeworkAttachment, attachment_id)
        if attachment is None:
            continue
        try:
            await _purge_staged_attachment(session, attachment, root)
            deleted_staged += 1
        except HTTPException:
            failures += 1
            logger.exception("Staged homework attachment cleanup will be retried")

    deleted_orphans, orphan_failures = await _cleanup_orphaned_files(
        session, root, datetime.now(UTC) - STAGED_ATTACHMENT_TTL
    )
    return CleanupSummary(
        deleted_staged=deleted_staged,
        deleted_orphans=deleted_orphans,
        failures=failures + orphan_failures,
    )


async def _cleanup_orphaned_files(
    session: AsyncSession, root: Path, older_than: datetime
) -> tuple[int, int]:
    try:
        known_keys = set(await session.scalars(select(HomeworkAttachment.storage_key)))
        paths = await asyncio.to_thread(lambda: list(root.iterdir()))
    except OSError, SQLAlchemyError:
        logger.exception("Unable to enumerate private homework storage")
        return 0, 1

    deleted = 0
    failures = 0
    for path in paths:
        is_temporary = (
            re.fullmatch(r"\.upload-[0-9a-f]{32}\.(?:part|lock)", path.name) is not None
        )
        is_orphan_key = (
            _STORAGE_KEY.fullmatch(path.name) is not None
            and path.name not in known_keys
        )
        if not (is_temporary or is_orphan_key):
            continue
        try:
            file_stat = await aiofiles.os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or datetime.fromtimestamp(file_stat.st_mtime, UTC) > older_than
            ):
                continue
            await aiofiles.os.remove(path)
            deleted += 1
        except FileNotFoundError:
            continue
        except OSError:
            logger.exception("Unable to remove an orphaned homework storage file")
            failures += 1

    return deleted, failures
