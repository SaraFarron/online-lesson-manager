import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.events.time_utils import normalize_utc
from backend.exceptions import Conflict, Forbidden, NotFound, ValidationError
from backend.homework.constants import (
    MAX_ASSIGNMENT_ATTACHMENT_BYTES,
    MAX_ASSIGNMENT_ATTACHMENTS,
)
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.homework.schemas import (
    HomeworkCompletionSource,
    HomeworkStatus,
    HomeworkSubmissionCreate,
    HomeworkSubmissionCreated,
)

logger = logging.getLogger(__name__)


def _submission_response(
    homework: Homework,
    submission: HomeworkSubmission,
) -> HomeworkSubmissionCreated:
    if (
        homework.status != HomeworkStatus.COMPLETE.value
        or homework.completion_source is None
    ):
        raise Conflict("Homework submission completion state is invalid")
    return HomeworkSubmissionCreated(
        version=submission.version,
        assignment_status=HomeworkStatus(homework.status),
        completion_source=HomeworkCompletionSource(homework.completion_source),
    )


async def _existing_submission(
    session: AsyncSession,
    homework_id: uuid.UUID,
    payload: HomeworkSubmissionCreate,
) -> HomeworkSubmissionCreated | None:
    try:
        homework = await session.get(Homework, homework_id)
        if (
            homework is None
            or homework.deleted_at is not None
            or homework.cleanup_pending
        ):
            return None

        submission = await session.scalar(
            select(HomeworkSubmission).where(
                HomeworkSubmission.homework_id == homework_id,
                HomeworkSubmission.operation_key == payload.operation_key,
            )
        )
        if submission is None:
            return None
        attachment_ids = set(
            await session.scalars(
                select(HomeworkAttachment.id).where(
                    HomeworkAttachment.submission_id == submission.id,
                )
            )
        )
        if submission.text != payload.text or attachment_ids != set(
            payload.attachment_ids
        ):
            raise Conflict("Operation key was already used for another submission")
        return _submission_response(homework, submission)
    except SQLAlchemyError as exc:
        logger.exception("Unable to reconcile homework submission operation")
        raise HTTPException(
            status_code=503,
            detail="Submission outcome could not be reconciled; retry with the same key",
        ) from exc


async def _load_staged_attachments(
    session: AsyncSession,
    student_id: uuid.UUID,
    attachment_ids: list[uuid.UUID],
) -> list[HomeworkAttachment]:
    if len(attachment_ids) > MAX_ASSIGNMENT_ATTACHMENTS:
        raise ValidationError("A submission may contain at most 5 attachments")
    if not attachment_ids:
        return []

    attachments = list(
        (
            await session.scalars(
                select(HomeworkAttachment)
                .where(
                    HomeworkAttachment.id.in_(attachment_ids),
                    HomeworkAttachment.owner_user_id == student_id,
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
        raise HTTPException(
            status_code=413, detail="Submission attachments exceed the 50 MiB limit"
        )
    return attachments


async def create_submission(
    session: AsyncSession,
    student: User,
    homework_id: uuid.UUID,
    payload: HomeworkSubmissionCreate,
) -> HomeworkSubmissionCreated:
    if student.role != Roles.STUDENT:
        raise Forbidden("Only students can submit homework")

    active_student = await session.scalar(
        select(User)
        .where(
            User.id == student.id,
            User.role == Roles.STUDENT,
            User.is_active.is_(True),
        )
        .with_for_update()
    )
    if active_student is None:
        raise Forbidden("Student account is not active")

    homework = await session.scalar(
        select(Homework)
        .where(
            Homework.id == homework_id,
            Homework.student_id == active_student.id,
            Homework.deleted_at.is_(None),
            Homework.cleanup_pending.is_(False),
        )
        .with_for_update()
    )
    if homework is None:
        raise NotFound("Homework not found")
    if active_student.teacher_id != homework.teacher_id:
        raise NotFound("Homework not found")
    active_teacher = await session.scalar(
        select(User.id).where(
            User.id == homework.teacher_id,
            User.role == Roles.TEACHER,
            User.is_active.is_(True),
        )
    )
    if active_teacher is None:
        raise NotFound("Homework not found")

    replay = await _existing_submission(session, homework_id, payload)
    if replay is not None:
        return replay
    if homework.status == HomeworkStatus.COMPLETE.value:
        if homework.completion_source == HomeworkCompletionSource.TEACHER.value:
            raise Conflict("Teacher-completed homework cannot receive a submission")
        if homework.completion_source != HomeworkCompletionSource.STUDENT_SUBMISSION:
            raise Conflict("Homework is not available for submission")

    try:
        attachments = await _load_staged_attachments(
            session, active_student.id, payload.attachment_ids
        )
    except NotFound:
        replay = await _existing_submission(session, homework_id, payload)
        if replay is not None:
            return replay
        raise

    current_version = await session.scalar(
        select(func.max(HomeworkSubmission.version)).where(
            HomeworkSubmission.homework_id == homework_id
        )
    )
    submission = HomeworkSubmission(
        id=uuid.uuid4(),
        homework_id=homework_id,
        student_id=active_student.id,
        version=(current_version or 0) + 1,
        text=payload.text,
        operation_key=payload.operation_key,
    )
    session.add(submission)
    try:
        await session.flush()
        for attachment in attachments:
            attachment.homework_id = homework_id
            attachment.submission_id = submission.id
            attachment.staged_until = None

        if homework.status == HomeworkStatus.INCOMPLETE.value:
            homework.status = HomeworkStatus.COMPLETE.value
            homework.completion_source = (
                HomeworkCompletionSource.STUDENT_SUBMISSION.value
            )
            homework.completed_at = datetime.now(UTC)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        replay = await _existing_submission(session, homework_id, payload)
        if replay is not None:
            return replay
        raise Conflict("Homework submission could not be finalized") from exc
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("Homework submission finalization outcome is unknown")
        replay = await _existing_submission(session, homework_id, payload)
        if replay is not None:
            return replay
        raise HTTPException(
            status_code=503,
            detail="Submission outcome is unknown; retry with the same key",
        ) from exc

    return _submission_response(homework, submission)
