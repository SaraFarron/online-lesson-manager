import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Exists

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.exceptions import Conflict, Forbidden, NotFound
from backend.homework import service as attachment_service
from backend.homework.config import HomeworkConfig
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.homework.schemas import (
    HomeworkCompletionPublic,
    HomeworkCompletionSource,
    HomeworkMaterialPublic,
    HomeworkStatus,
    HomeworkSubmissionPage,
    HomeworkSubmissionReviewItem,
)

logger = logging.getLogger(__name__)


def _active_student_relationship() -> Exists:
    return (
        select(User.id)
        .where(
            User.id == Homework.student_id,
            User.role == Roles.STUDENT,
            User.is_active.is_(True),
            User.teacher_id == Homework.teacher_id,
        )
        .exists()
    )


async def _active_teacher_homework(
    session: AsyncSession,
    teacher: User,
    homework_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Homework:
    if teacher.role != Roles.TEACHER:
        raise Forbidden("Only teachers can manage homework")
    statement = select(Homework).where(
        Homework.id == homework_id,
        Homework.teacher_id == teacher.id,
        Homework.deleted_at.is_(None),
        Homework.cleanup_pending.is_(False),
        _active_student_relationship(),
    )
    if lock:
        statement = statement.with_for_update()
    homework = await session.scalar(statement)
    if homework is None:
        raise NotFound("Homework not found")
    return homework


def _completion_response(homework: Homework) -> HomeworkCompletionPublic:
    if (
        homework.status != HomeworkStatus.COMPLETE.value
        or homework.completion_source is None
        or homework.completed_at is None
    ):
        raise Conflict("Homework completion state is invalid")
    return HomeworkCompletionPublic(
        status=HomeworkStatus(homework.status),
        completion_source=HomeworkCompletionSource(homework.completion_source),
        completed_at=homework.completed_at,
    )


async def list_submission_reviews(
    session: AsyncSession,
    teacher: User,
    homework_id: uuid.UUID,
    *,
    skip: int,
    limit: int,
) -> HomeworkSubmissionPage:
    homework = await _active_teacher_homework(session, teacher, homework_id)
    total = await session.scalar(
        select(func.count(HomeworkSubmission.id)).where(
            HomeworkSubmission.homework_id == homework.id
        )
    )
    latest_version = (
        select(func.max(HomeworkSubmission.version))
        .where(HomeworkSubmission.homework_id == homework.id)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(
                HomeworkSubmission,
                (HomeworkSubmission.version == latest_version).label("is_current"),
            )
            .where(HomeworkSubmission.homework_id == homework.id)
            .order_by(HomeworkSubmission.version.desc())
            .offset(skip)
            .limit(limit)
        )
    ).all()
    submissions = [submission for submission, _ in rows]
    attachment_map: dict[uuid.UUID, list[HomeworkMaterialPublic]] = {
        submission.id: [] for submission in submissions
    }
    if submissions:
        attachment_rows = (
            await session.execute(
                select(
                    HomeworkAttachment.submission_id,
                    HomeworkAttachment.id,
                    HomeworkAttachment.file_name,
                    HomeworkAttachment.byte_size,
                )
                .where(
                    HomeworkAttachment.homework_id == homework.id,
                    HomeworkAttachment.submission_id.in_(
                        [submission.id for submission in submissions]
                    ),
                    HomeworkAttachment.cleanup_pending.is_(False),
                )
                .order_by(HomeworkAttachment.created_at, HomeworkAttachment.id)
            )
        ).all()
        for submission_id, attachment_id, file_name, byte_size in attachment_rows:
            if submission_id is not None:
                attachment_map[submission_id].append(
                    HomeworkMaterialPublic(
                        id=attachment_id,
                        file_name=file_name,
                        byte_size=byte_size,
                    )
                )

    return HomeworkSubmissionPage(
        homework_id=homework.id,
        items=[
            HomeworkSubmissionReviewItem(
                id=submission.id,
                version=submission.version,
                text=submission.text,
                created_at=submission.created_at,
                is_current=is_current,
                materials=attachment_map[submission.id],
            )
            for submission, is_current in rows
        ],
        total=total or 0,
    )


async def complete_homework(
    session: AsyncSession,
    teacher: User,
    homework_id: uuid.UUID,
) -> HomeworkCompletionPublic:
    homework = await _active_teacher_homework(session, teacher, homework_id, lock=True)
    if homework.status == HomeworkStatus.COMPLETE.value:
        if homework.completion_source == HomeworkCompletionSource.TEACHER.value:
            return _completion_response(homework)
        raise Conflict("Homework has already been completed by the student")
    if homework.status != HomeworkStatus.INCOMPLETE.value:
        raise Conflict("Homework is not available for completion")

    homework.status = HomeworkStatus.COMPLETE.value
    homework.completion_source = HomeworkCompletionSource.TEACHER.value
    homework.completed_at = datetime.now(UTC)
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        try:
            completed = await session.scalar(
                select(Homework).where(
                    Homework.id == homework_id,
                    Homework.teacher_id == teacher.id,
                    Homework.deleted_at.is_(None),
                    Homework.cleanup_pending.is_(False),
                )
            )
        except SQLAlchemyError as reconcile_error:
            logger.exception("Unable to reconcile teacher homework completion")
            raise HTTPException(
                status_code=503,
                detail="Completion outcome could not be reconciled",
            ) from reconcile_error
        if completed is not None:
            if completed.completion_source == HomeworkCompletionSource.TEACHER.value:
                return _completion_response(completed)
            if completed.completion_source == (
                HomeworkCompletionSource.STUDENT_SUBMISSION.value
            ):
                raise Conflict("Homework has already been completed by the student")
        logger.exception("Teacher homework completion outcome is unknown")
        raise HTTPException(
            status_code=503,
            detail="Completion outcome is unknown; retry the request",
        ) from exc
    return _completion_response(homework)


async def _active_student_link_exists(
    session: AsyncSession,
    homework: Homework,
) -> bool:
    student_id = await session.scalar(
        select(User.id).where(
            User.id == homework.student_id,
            User.role == Roles.STUDENT,
            User.is_active.is_(True),
            User.teacher_id == homework.teacher_id,
        )
    )
    return student_id is not None


async def _load_cleanup_tombstone(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    homework_id: uuid.UUID,
    *,
    lock: bool,
) -> Homework | None:
    statement = select(Homework).where(
        Homework.id == homework_id,
        Homework.teacher_id == teacher_id,
        Homework.deleted_at.is_not(None),
        Homework.cleanup_pending.is_(True),
    )
    if lock:
        statement = statement.with_for_update()
    result = await session.scalars(statement)
    return result.one_or_none()


async def delete_homework(
    session: AsyncSession,
    teacher: User,
    homework_id: uuid.UUID,
    config: HomeworkConfig,
) -> None:
    if teacher.role != Roles.TEACHER:
        raise Forbidden("Only teachers can delete homework")
    assignment = await session.scalar(
        select(Homework)
        .where(
            Homework.id == homework_id,
            Homework.teacher_id == teacher.id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise NotFound("Homework not found")

    if assignment.deleted_at is None or not assignment.cleanup_pending:
        if assignment.deleted_at is None:
            if not await _active_student_link_exists(session, assignment):
                raise NotFound("Homework not found")
            assignment.deleted_at = datetime.now(UTC)
        assignment.cleanup_pending = True
        attachments = list(
            (
                await session.scalars(
                    select(HomeworkAttachment)
                    .where(HomeworkAttachment.homework_id == homework_id)
                    .with_for_update()
                )
            ).all()
        )
        for attachment in attachments:
            attachment.cleanup_pending = True
        try:
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            try:
                tombstone = await _load_cleanup_tombstone(
                    session, teacher.id, homework_id, lock=False
                )
            except SQLAlchemyError as reconcile_error:
                logger.exception("Unable to reconcile homework deletion request")
                raise HTTPException(
                    status_code=503,
                    detail="Deletion outcome could not be reconciled; retry",
                ) from reconcile_error
            if tombstone is None:
                logger.exception("Unable to persist homework deletion tombstone")
                raise HTTPException(
                    status_code=503,
                    detail="Homework could not be hidden; retry deletion",
                ) from exc

    try:
        tombstone = await _load_cleanup_tombstone(
            session, teacher.id, homework_id, lock=True
        )
        if tombstone is None:
            return
        attachments = list(
            (
                await session.scalars(
                    select(HomeworkAttachment)
                    .where(HomeworkAttachment.homework_id == homework_id)
                    .with_for_update()
                )
            ).all()
        )
        await attachment_service.remove_committed_attachment_files(attachments, config)
        await session.execute(
            delete(HomeworkAttachment).where(
                HomeworkAttachment.homework_id == homework_id
            )
        )
        await session.execute(
            delete(HomeworkSubmission).where(
                HomeworkSubmission.homework_id == homework_id
            )
        )
        await session.execute(
            delete(Homework).where(
                Homework.id == homework_id,
                Homework.teacher_id == teacher.id,
                Homework.deleted_at.is_not(None),
                Homework.cleanup_pending.is_(True),
            )
        )
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("Homework cleanup could not be finalized")
        raise HTTPException(
            status_code=503,
            detail="Homework is hidden; file and metadata cleanup can be retried",
        ) from exc
