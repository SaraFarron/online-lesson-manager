import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.events.time_utils import normalize_utc
from backend.exceptions import Forbidden, NotFound, ValidationError
from backend.homework.models import Homework, HomeworkAttachment
from backend.homework.schemas import (
    HomeworkCompletionSource,
    HomeworkMaterialPublic,
    HomeworkPage,
    HomeworkReadDetail,
    HomeworkReadItem,
    HomeworkStatus,
    HomeworkStudentSummary,
    LessonOccurrenceReference,
    LessonSourceKind,
)


def _stored_lesson_reference(homework: Homework) -> LessonOccurrenceReference | None:
    values = (
        homework.lesson_kind,
        homework.lesson_source_id,
        homework.lesson_occurrence_start,
    )
    if all(value is None for value in values):
        return None
    if (
        homework.lesson_kind is None
        or homework.lesson_source_id is None
        or homework.lesson_occurrence_start is None
    ):
        raise ValidationError("Homework has an invalid lesson association")
    try:
        kind = LessonSourceKind(homework.lesson_kind)
    except ValueError as exc:
        raise ValidationError("Homework has an invalid lesson source kind") from exc

    return LessonOccurrenceReference(
        kind=kind,
        source_id=homework.lesson_source_id,
        starts_at=normalize_utc(homework.lesson_occurrence_start),
    )


async def _visibility_filters(
    session: AsyncSession,
    viewer: User,
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [
        Homework.deleted_at.is_(None),
        Homework.cleanup_pending.is_(False),
    ]
    active_student_relationship = (
        select(User.id)
        .where(
            User.id == Homework.student_id,
            User.role == Roles.STUDENT,
            User.is_active.is_(True),
            User.teacher_id == Homework.teacher_id,
        )
        .exists()
    )
    filters.append(active_student_relationship)

    if viewer.role == Roles.TEACHER:
        filters.append(Homework.teacher_id == viewer.id)
        return filters

    if viewer.role == Roles.STUDENT:
        if viewer.teacher_id is None:
            raise Forbidden("Student must have an active teacher link to read homework")
        active_teacher = await session.scalar(
            select(User.id).where(
                User.id == viewer.teacher_id,
                User.role == Roles.TEACHER,
                User.is_active.is_(True),
            )
        )
        if active_teacher is None:
            raise Forbidden("Student must have an active teacher link to read homework")
        filters.extend(
            (
                Homework.student_id == viewer.id,
                Homework.teacher_id == viewer.teacher_id,
            )
        )
        return filters

    raise Forbidden("Only teachers and students can read homework")


def _homework_item(
    homework: Homework,
    student_name: str | None,
    material_count: int,
    viewer: User,
) -> HomeworkReadItem:
    completion_source = (
        HomeworkCompletionSource(homework.completion_source)
        if homework.completion_source is not None
        else None
    )
    student = (
        HomeworkStudentSummary(id=homework.student_id, full_name=student_name)
        if viewer.role == Roles.TEACHER
        else None
    )
    return HomeworkReadItem(
        id=homework.id,
        student=student,
        text=homework.text,
        status=HomeworkStatus(homework.status),
        completion_source=completion_source,
        created_at=homework.created_at,
        updated_at=homework.updated_at,
        completed_at=homework.completed_at,
        lesson_occurrence=_stored_lesson_reference(homework),
        material_count=material_count,
    )


def _student_name_subquery() -> ScalarSelect[str | None]:
    return (
        select(User.full_name).where(User.id == Homework.student_id).scalar_subquery()
    )


def _material_count_subquery() -> ScalarSelect[int]:
    return (
        select(func.count(HomeworkAttachment.id))
        .where(
            HomeworkAttachment.homework_id == Homework.id,
            HomeworkAttachment.submission_id.is_(None),
            HomeworkAttachment.cleanup_pending.is_(False),
        )
        .scalar_subquery()
    )


async def list_homework(
    session: AsyncSession,
    viewer: User,
    *,
    skip: int,
    limit: int,
    status_filter: HomeworkStatus | None,
) -> HomeworkPage:
    filters = await _visibility_filters(session, viewer)
    if status_filter is not None:
        filters.append(Homework.status == status_filter.value)

    total = await session.scalar(select(func.count(Homework.id)).where(*filters))
    rows = (
        await session.execute(
            select(
                Homework,
                _student_name_subquery(),
                _material_count_subquery(),
            )
            .where(*filters)
            .order_by(Homework.created_at.desc(), Homework.id.desc())
            .offset(skip)
            .limit(limit)
        )
    ).all()

    return HomeworkPage(
        items=[
            _homework_item(homework, student_name, material_count, viewer)
            for homework, student_name, material_count in rows
        ],
        total=total or 0,
    )


async def get_homework_detail(
    session: AsyncSession,
    viewer: User,
    homework_id: uuid.UUID,
) -> HomeworkReadDetail:
    filters = await _visibility_filters(session, viewer)
    row = (
        await session.execute(
            select(Homework, _student_name_subquery()).where(
                Homework.id == homework_id, *filters
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFound("Homework not found")

    homework, student_name = row
    materials = (
        await session.execute(
            select(
                HomeworkAttachment.id,
                HomeworkAttachment.file_name,
                HomeworkAttachment.byte_size,
            )
            .where(
                HomeworkAttachment.homework_id == homework.id,
                HomeworkAttachment.submission_id.is_(None),
                HomeworkAttachment.cleanup_pending.is_(False),
            )
            .order_by(HomeworkAttachment.created_at, HomeworkAttachment.id)
        )
    ).all()
    item = _homework_item(homework, student_name, len(materials), viewer)
    return HomeworkReadDetail(
        **item.model_dump(),
        materials=[
            HomeworkMaterialPublic(
                id=attachment_id, file_name=file_name, byte_size=size
            )
            for attachment_id, file_name, size in materials
        ],
    )
