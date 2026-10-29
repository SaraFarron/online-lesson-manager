import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events import range_service as event_range_service
from backend.events.occurrences import OccurrenceSourceKind
from backend.events.time_utils import normalize_utc, to_storage_utc
from backend.exceptions import Conflict, Forbidden, NotFound, ValidationError
from backend.homework.models import Homework, HomeworkAttachment
from backend.homework.schemas import (
    HomeworkCreate,
    HomeworkCreated,
    HomeworkStatus,
    LessonOccurrenceReference,
)
from backend.homework.services.staged_attachments import load_staged_attachments

logger = logging.getLogger(__name__)


def _lesson_values(
    reference: LessonOccurrenceReference | None,
) -> tuple[str | None, uuid.UUID | None, datetime | None]:
    if reference is None:
        return None, None, None
    return (
        reference.kind.value,
        reference.source_id,
        to_storage_utc(normalize_utc(reference.starts_at)),
    )


def _created_response(homework: Homework, material_count: int) -> HomeworkCreated:
    return HomeworkCreated(
        id=homework.id,
        status=HomeworkStatus(homework.status),
        material_count=material_count,
    )


async def _existing_result(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    payload: HomeworkCreate,
    operation_key: uuid.UUID,
) -> HomeworkCreated | None:
    try:
        homework = await session.scalar(
            select(Homework).where(
                Homework.teacher_id == teacher_id,
                Homework.operation_key == operation_key,
            )
        )
        if homework is None:
            return None
        if homework.deleted_at is not None or homework.cleanup_pending:
            raise Conflict("Assignment operation is no longer available")

        lesson_kind, lesson_source_id, lesson_start = _lesson_values(
            payload.lesson_occurrence
        )
        matches = (
            homework.student_id == payload.student_id
            and homework.text == payload.text
            and homework.lesson_kind == lesson_kind
            and homework.lesson_source_id == lesson_source_id
            and (
                homework.lesson_occurrence_start is None
                and lesson_start is None
                or homework.lesson_occurrence_start is not None
                and lesson_start is not None
                and to_storage_utc(normalize_utc(homework.lesson_occurrence_start))
                == lesson_start
            )
        )
        if not matches:
            raise Conflict("Idempotency key was already used for another assignment")

        attachment_ids = set(
            await session.scalars(
                select(HomeworkAttachment.id).where(
                    HomeworkAttachment.homework_id == homework.id,
                    HomeworkAttachment.submission_id.is_(None),
                )
            )
        )
        if attachment_ids != set(payload.attachment_ids):
            raise Conflict("Idempotency key was already used for another assignment")
        return _created_response(homework, len(attachment_ids))
    except SQLAlchemyError as exc:
        logger.exception("Unable to reconcile homework assignment operation")
        raise HTTPException(
            status_code=503,
            detail="Assignment outcome could not be reconciled; retry with the same key",
        ) from exc


async def _validate_lesson_occurrence(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
    reference: LessonOccurrenceReference | None,
) -> tuple[str | None, uuid.UUID | None, datetime | None]:
    values = _lesson_values(reference)
    if reference is None:
        return values

    starts_at = normalize_utc(reference.starts_at)
    if starts_at <= datetime.now(UTC):
        raise ValidationError("Lesson occurrence must be in the future")
    try:
        range_end = starts_at + timedelta(microseconds=1)
    except OverflowError as exc:
        raise ValidationError(
            "Lesson occurrence is outside the supported range"
        ) from exc

    source_kind = (
        OccurrenceSourceKind.EVENT
        if reference.kind.value == "EVENT"
        else OccurrenceSourceKind.RECURRENT_EVENT
    )
    occurrences = await event_range_service.load_raw_occurrences_for_validation(
        session,
        teacher_id,
        starts_at,
        range_end,
    )
    is_active_lesson = any(
        occurrence.identity == (source_kind, reference.source_id, starts_at)
        and occurrence.source.event_type == EventType.LESSON
        and occurrence.source.user_id == student_id
        for occurrence in occurrences
    )
    if not is_active_lesson:
        raise ValidationError("Active lesson occurrence was not found")
    return values


async def create_assignment(
    session: AsyncSession,
    teacher: User,
    payload: HomeworkCreate,
    operation_key: uuid.UUID,
) -> HomeworkCreated:
    if teacher.role != Roles.TEACHER:
        raise Forbidden("Only teachers can create homework assignments")
    teacher_id = teacher.id

    student = await session.scalar(
        select(User)
        .where(
            User.id == payload.student_id,
            User.role == Roles.STUDENT,
            User.is_active.is_(True),
            User.teacher_id == teacher_id,
        )
        .with_for_update()
    )
    if student is None:
        raise Forbidden("Student is not actively linked to this teacher")

    replay = await _existing_result(session, teacher_id, payload, operation_key)
    if replay is not None:
        return replay

    lesson_kind, lesson_source_id, lesson_start = await _validate_lesson_occurrence(
        session,
        teacher_id,
        student.id,
        payload.lesson_occurrence,
    )
    try:
        attachments = await load_staged_attachments(
            session,
            teacher_id,
            payload.attachment_ids,
            count_error="An assignment may contain at most 5 attachments",
            size_error="Assignment attachments exceed the 50 MiB limit",
        )
    except NotFound:
        replay = await _existing_result(session, teacher_id, payload, operation_key)
        if replay is not None:
            return replay
        raise

    homework = Homework(
        id=uuid.uuid4(),
        teacher_id=teacher_id,
        student_id=student.id,
        text=payload.text,
        lesson_kind=lesson_kind,
        lesson_source_id=lesson_source_id,
        lesson_occurrence_start=lesson_start,
        operation_key=operation_key,
    )
    session.add(homework)
    for attachment in attachments:
        attachment.homework_id = homework.id
        attachment.staged_until = None

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        replay = await _existing_result(session, teacher_id, payload, operation_key)
        if replay is not None:
            return replay
        raise Conflict("Homework assignment could not be finalized") from exc
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("Homework assignment finalization outcome is unknown")
        replay = await _existing_result(session, teacher_id, payload, operation_key)
        if replay is not None:
            return replay
        raise HTTPException(
            status_code=503,
            detail="Assignment outcome is unknown; retry with the same key",
        ) from exc

    return _created_response(homework, len(attachments))
