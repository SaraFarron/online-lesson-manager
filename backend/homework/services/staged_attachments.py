import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.time_utils import normalize_utc
from backend.exceptions import NotFound, ValidationError
from backend.homework.constants import (
    MAX_ASSIGNMENT_ATTACHMENT_BYTES,
    MAX_ASSIGNMENT_ATTACHMENTS,
)
from backend.homework.models import HomeworkAttachment


async def load_staged_attachments(
    session: AsyncSession,
    owner_user_id: uuid.UUID,
    attachment_ids: list[uuid.UUID],
    *,
    count_error: str,
    size_error: str,
) -> list[HomeworkAttachment]:
    if len(attachment_ids) > MAX_ASSIGNMENT_ATTACHMENTS:
        raise ValidationError(count_error)
    if not attachment_ids:
        return []

    attachments = list(
        (
            await session.scalars(
                select(HomeworkAttachment)
                .where(
                    HomeworkAttachment.id.in_(attachment_ids),
                    HomeworkAttachment.owner_user_id == owner_user_id,
                    HomeworkAttachment.homework_id.is_(None),
                    HomeworkAttachment.submission_id.is_(None),
                    HomeworkAttachment.cleanup_pending.is_(False),
                )
                .with_for_update()
            )
        ).all()
    )
    if len(attachments) != len(attachment_ids):
        raise NotFound("Staged attachment not found")

    now = datetime.now(UTC)
    if any(
        attachment.staged_until is None or normalize_utc(attachment.staged_until) <= now
        for attachment in attachments
    ):
        raise HTTPException(status_code=410, detail="Staged attachment has expired")
    if sum(attachment.byte_size for attachment in attachments) > (
        MAX_ASSIGNMENT_ATTACHMENT_BYTES
    ):
        raise HTTPException(status_code=413, detail=size_error)
    return attachments
