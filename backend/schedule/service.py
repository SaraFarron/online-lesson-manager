from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.exceptions import InactiveUser
from backend.auth.models import User
from backend.constants import EventType
from backend.events.occurrences import Occurrence
from backend.events.range_service import load_raw_occurrences
from backend.events.time_utils import (
    get_timezone,
    local_day_bounds,
    local_week_bounds,
    normalize_utc,
)
from backend.events.visibility import filter_vacation_visibility
from backend.exceptions import BadRequest, Conflict, Forbidden
from backend.schedule.presentation import shape_schedule
from backend.schedule.schemas import SchedulePeriod, ScheduleResponse

UNLINKED_STUDENT_DETAIL = "Student is not linked to a teacher"
INVALID_LINKED_TEACHER_DETAIL = "Linked teacher cannot provide a schedule"
UNSUPPORTED_SCHEDULE_ROLE_DETAIL = "Only teachers and students can view schedules"
UNNAMED_STUDENT = "Unnamed student"
PERSONAL_LESSON = "Personal lesson"


async def get_schedule(
    session: AsyncSession,
    viewer: User,
    period: SchedulePeriod,
    requested_date: date | None,
    generated_at: datetime,
) -> ScheduleResponse:
    """Load, authorize, and shape one viewer-local day or week snapshot."""
    if not viewer.is_active:
        raise InactiveUser()

    teacher_id = await _resolve_teacher_id(session, viewer)
    now = normalize_utc(generated_at)
    zone = get_timezone(viewer.timezone)
    schedule_date = requested_date or now.astimezone(zone).date()
    if period == SchedulePeriod.DAY:
        range_start, range_end = local_day_bounds(schedule_date, viewer.timezone)
    else:
        range_start, range_end = local_week_bounds(schedule_date, viewer.timezone)

    raw_occurrences = await load_raw_occurrences(
        session,
        teacher_id,
        range_start,
        range_end,
        include_automatic_breaks=True,
    )
    visible_occurrences = filter_vacation_visibility(
        raw_occurrences, teacher_id
    ).visible

    is_student = viewer.role == Roles.STUDENT
    authorized_occurrences = (
        tuple(
            occurrence
            for occurrence in visible_occurrences
            if occurrence.source.user_id == viewer.id
        )
        if is_student
        else visible_occurrences
    )
    subject_labels = (
        await _load_teacher_subject_labels(session, teacher_id, authorized_occurrences)
        if not is_student
        else None
    )

    return shape_schedule(
        period,
        schedule_date,
        viewer.timezone,
        now,
        authorized_occurrences,
        subject_labels=subject_labels,
    )


async def _resolve_teacher_id(session: AsyncSession, viewer: User) -> UUID:
    if viewer.role == Roles.TEACHER:
        return viewer.id
    if viewer.role != Roles.STUDENT:
        raise Forbidden(UNSUPPORTED_SCHEDULE_ROLE_DETAIL)
    if viewer.teacher_id is None:
        raise BadRequest(UNLINKED_STUDENT_DETAIL)

    linked_teacher = await session.execute(
        select(User.role, User.is_active).where(User.id == viewer.teacher_id)
    )
    teacher_state = linked_teacher.one_or_none()
    if (
        teacher_state is None
        or teacher_state.role != Roles.TEACHER
        or not teacher_state.is_active
    ):
        raise Conflict(INVALID_LINKED_TEACHER_DETAIL)
    return viewer.teacher_id


async def _load_teacher_subject_labels(
    session: AsyncSession,
    teacher_id: UUID,
    occurrences: tuple[Occurrence, ...],
) -> dict[UUID, str]:
    lesson_subject_ids = {
        occurrence.source.user_id
        for occurrence in occurrences
        if occurrence.source.event_type == EventType.LESSON
    }
    if not lesson_subject_ids:
        return {}

    labels: dict[UUID, str] = {}
    if teacher_id in lesson_subject_ids:
        labels[teacher_id] = PERSONAL_LESSON

    student_ids = lesson_subject_ids - {teacher_id}
    if student_ids:
        result = await session.execute(
            select(User.id, User.full_name).where(
                User.id.in_(student_ids),
                User.role == Roles.STUDENT,
                User.teacher_id == teacher_id,
            )
        )
        for student_id, full_name in result.all():
            labels[student_id] = (
                full_name.strip()
                if full_name and full_name.strip()
                else UNNAMED_STUDENT
            )

        for student_id in student_ids:
            labels.setdefault(student_id, UNNAMED_STUDENT)

    return labels
