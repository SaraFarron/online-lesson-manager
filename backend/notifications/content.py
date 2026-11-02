from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events.time_utils import get_timezone, normalize_utc
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.notifications.constants import NotificationType
from backend.notifications.context import get_daily_digest_context
from backend.notifications.models import NotificationIntent, TeacherBroadcast
from backend.schedule import service as schedule_service
from backend.schedule.schemas import (
    ScheduleItem,
    SchedulePeriod,
    ScheduleResponse,
    TeacherLessonScheduleItem,
)

MAX_HOMEWORK_EXCERPT_LENGTH = 500


async def build_notification_content(
    session: AsyncSession,
    recipient: User,
    intent: NotificationIntent,
    now: datetime,
) -> list[str]:
    kind = NotificationType(intent.notification_type)
    if kind == NotificationType.DAILY_DIGEST:
        if intent.source_date is None:
            raise RuntimeError("Daily digest is missing its source date")
        context = await get_daily_digest_context(
            session, recipient, intent.source_date, now
        )
        digest_lines = _schedule_lines(
            context.schedule,
            recipient.timezone,
            recipient.role == Roles.TEACHER,
        )
        if recipient.role == Roles.STUDENT:
            for item in context.schedule.days[0].items:
                if item.event_type != EventType.LESSON:
                    continue
                identity = (
                    item.source_kind,
                    item.source_id,
                    normalize_utc(item.start),
                )
                statuses = context.homework_status_by_lesson.get(identity, ())
                incomplete = sum(status.value == "INCOMPLETE" for status in statuses)
                if incomplete:
                    local_start = item.start.astimezone(
                        get_timezone(recipient.timezone)
                    )
                    digest_lines.append(
                        f"Incomplete homework for {local_start:%H:%M} lesson: "
                        f"{incomplete}"
                    )
        digest_lines.insert(0, f"Schedule for {intent.source_date.isoformat()}")
        return _with_open_instruction(digest_lines)

    if kind == NotificationType.LESSON_REMINDER:
        schedule_date = (
            intent.occurrence_start_at.astimezone(
                get_timezone(recipient.timezone)
            ).date()
            if intent.occurrence_start_at is not None
            else None
        )
        if schedule_date is None:
            raise RuntimeError("Lesson reminder is missing its occurrence")
        schedule = await _schedule_for_date(session, recipient, schedule_date, now)
        item = _matching_occurrence(schedule, intent)
        local_start = item.start.astimezone(get_timezone(recipient.timezone))
        local_end = item.end.astimezone(get_timezone(recipient.timezone))
        return _with_open_instruction(
            [
                f"Your lesson starts {local_start:%A, %Y-%m-%d at %H:%M} "
                f"and ends at {local_end:%H:%M} ({recipient.timezone})."
            ]
        )

    if kind == NotificationType.TEACHER_BROADCAST:
        if intent.source_id is None:
            raise RuntimeError("Teacher broadcast is missing its source")
        broadcast = await session.get(TeacherBroadcast, intent.source_id)
        if broadcast is None:
            raise RuntimeError("Validated teacher broadcast disappeared during claim")
        return [broadcast.message]

    if intent.source_id is None:
        raise RuntimeError("Homework notification is missing its source")
    homework = await session.scalar(
        select(Homework).where(
            Homework.id == intent.source_id,
            Homework.deleted_at.is_(None),
            Homework.cleanup_pending.is_(False),
        )
    )
    if homework is None:
        raise RuntimeError("Validated homework source disappeared during claim")
    attachment_names = await _homework_attachment_names(session, homework.id)
    homework_lines: list[str]

    if kind == NotificationType.ASSIGNMENT_CREATED:
        teacher = await session.get(User, homework.teacher_id)
        if teacher is None:
            raise RuntimeError("Validated homework teacher disappeared during claim")
        homework_lines = [f"From: {_display_name(teacher.full_name, 'your teacher')}"]
        homework_lines.extend(_homework_description(homework.text, attachment_names))
    elif kind == NotificationType.HOMEWORK_REMINDER:
        if homework.lesson_occurrence_start is None:
            raise RuntimeError("Validated homework has no linked lesson")
        local_start = normalize_utc(homework.lesson_occurrence_start).astimezone(
            get_timezone(recipient.timezone)
        )
        homework_lines = [
            f"Lesson: {local_start:%A, %Y-%m-%d at %H:%M} ({recipient.timezone})"
        ]
        homework_lines.extend(_homework_description(homework.text, attachment_names))
    elif kind == NotificationType.SUBMISSION_RECEIVED:
        if intent.source_version is None:
            raise RuntimeError("Submission notification is missing its version")
        submission_row = await session.execute(
            select(HomeworkSubmission, User.full_name)
            .join(User, User.id == HomeworkSubmission.student_id)
            .where(
                HomeworkSubmission.homework_id == homework.id,
                HomeworkSubmission.version == intent.source_version,
                HomeworkSubmission.student_id == homework.student_id,
            )
        )
        submission_and_name = submission_row.one_or_none()
        if submission_and_name is None:
            raise RuntimeError("Validated homework submission disappeared during claim")
        submission, student_name = submission_and_name
        submission_files = await _submission_attachment_names(session, submission.id)
        homework_lines = [
            f"From: {_display_name(student_name, 'your student')}",
            f"Solution version: {submission.version}",
            "Assignment:",
        ]
        homework_lines.extend(_homework_description(homework.text, attachment_names))
        homework_lines.append("Student solution:")
        homework_lines.extend(_homework_description(submission.text, submission_files))
    else:
        raise RuntimeError(f"Unsupported notification content type: {kind}")
    return _with_open_instruction(homework_lines)


async def _schedule_for_date(
    session: AsyncSession, recipient: User, schedule_date: date, now: datetime
) -> ScheduleResponse:
    return await schedule_service.get_schedule(
        session, recipient, SchedulePeriod.DAY, schedule_date, now
    )


def _schedule_lines(
    schedule: ScheduleResponse, timezone: str, include_subjects: bool
) -> list[str]:
    lines: list[str] = []
    zone = get_timezone(timezone)
    for item in schedule.days[0].items:
        lines.append(_format_schedule_item(item, zone, include_subjects))
    return lines or ["No events are scheduled."]


def _format_schedule_item(
    item: ScheduleItem, zone: ZoneInfo, include_subjects: bool
) -> str:
    start = item.start.astimezone(zone)
    end = item.end.astimezone(zone)
    event_name = item.event_type.value.replace("_", " ").title()
    subject = ""
    if include_subjects and isinstance(item, TeacherLessonScheduleItem):
        subject = f" — {item.subject_label}"
    return f"{start:%H:%M}–{end:%H:%M} {event_name}{subject}"


def _matching_occurrence(
    schedule: ScheduleResponse, intent: NotificationIntent
) -> ScheduleItem:
    if (
        intent.occurrence_source_type is None
        or intent.occurrence_source_id is None
        or intent.occurrence_start_at is None
    ):
        raise RuntimeError("Lesson reminder is missing its source occurrence")
    match = next(
        (
            item
            for item in schedule.days[0].items
            if item.event_type == EventType.LESSON
            and item.source_kind.value == intent.occurrence_source_type
            and item.source_id == intent.occurrence_source_id
            and normalize_utc(item.start) == normalize_utc(intent.occurrence_start_at)
        ),
        None,
    )
    if match is None:
        raise RuntimeError("Validated lesson occurrence disappeared during claim")
    return match


async def _homework_attachment_names(
    session: AsyncSession, homework_id: UUID
) -> list[str]:
    return list(
        await session.scalars(
            select(HomeworkAttachment.file_name)
            .where(
                HomeworkAttachment.homework_id == homework_id,
                HomeworkAttachment.submission_id.is_(None),
                HomeworkAttachment.cleanup_pending.is_(False),
            )
            .order_by(HomeworkAttachment.created_at, HomeworkAttachment.id)
        )
    )


async def _submission_attachment_names(
    session: AsyncSession, submission_id: UUID
) -> list[str]:
    return list(
        await session.scalars(
            select(HomeworkAttachment.file_name)
            .where(
                HomeworkAttachment.submission_id == submission_id,
                HomeworkAttachment.cleanup_pending.is_(False),
            )
            .order_by(HomeworkAttachment.created_at, HomeworkAttachment.id)
        )
    )


def _homework_description(text: str | None, attachment_names: list[str]) -> list[str]:
    lines: list[str] = []
    excerpt = _safe_excerpt(text)
    if excerpt:
        lines.append(f"Text: {excerpt}")
    if attachment_names:
        lines.append("Materials: " + ", ".join(attachment_names))
    if not lines:
        lines.append("No text or materials are available.")
    return lines


def _safe_excerpt(value: str | None) -> str:
    if value is None:
        return ""
    normalized = " ".join(value.split())
    if len(normalized) > MAX_HOMEWORK_EXCERPT_LENGTH:
        return normalized[:MAX_HOMEWORK_EXCERPT_LENGTH].rstrip() + "…"
    return normalized


def _display_name(value: str | None, fallback: str) -> str:
    return value.strip() if value and value.strip() else fallback


def _with_open_instruction(lines: list[str]) -> list[str]:
    return [*lines, "Open /start in Telegram to view the related details."]
