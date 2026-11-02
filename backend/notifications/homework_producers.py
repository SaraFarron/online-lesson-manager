import logging
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from backend.auth.constants import Roles
from backend.auth.models import StudentProfile, User
from backend.constants import EventType
from backend.events.time_utils import (
    get_timezone,
    local_day_bounds,
    normalize_utc,
    resolve_local_datetime,
    to_storage_utc,
)
from backend.exceptions import BadRequest, Conflict, Forbidden, ValidationError
from backend.homework.models import Homework
from backend.notifications.constants import NotificationType
from backend.notifications.producer_utils import insert_notification_intents
from backend.schedule import service as schedule_service
from backend.schedule.schemas import ScheduleItem, SchedulePeriod

logger = logging.getLogger(__name__)
REMINDER_CATCH_UP = timedelta(minutes=15)
TARGET_BATCH_SIZE = 100


async def produce_homework_reminders(
    session: AsyncSession,
    now: datetime | None = None,
) -> None:
    timestamp = normalize_utc(now or datetime.now(UTC))
    linked_teacher = aliased(User)
    has_active_teacher = exists(
        select(linked_teacher.id).where(
            linked_teacher.id == User.teacher_id,
            linked_teacher.role == Roles.TEACHER,
            linked_teacher.is_active.is_(True),
        )
    )
    students = list(
        await session.scalars(
            select(User)
            .join(StudentProfile, StudentProfile.student_id == User.id)
            .where(
                User.role == Roles.STUDENT,
                User.is_active.is_(True),
                User.telegram_private_chat_id.is_not(None),
                User.notification_lessons_today.is_not(None),
                has_active_teacher,
                StudentProfile.notification_homework.is_not(None),
            )
            .options(selectinload(User.student_profile))
            .order_by(User.id)
        )
    )

    targets: dict[uuid.UUID, tuple[User, date, date, datetime, datetime, datetime]] = {}
    for student in students:
        profile = student.student_profile
        if profile is None or profile.notification_homework is None:
            continue
        scheduled_at = _homework_reminder_due_at(student, profile, timestamp)
        if scheduled_at is None:
            continue
        local_today = timestamp.astimezone(get_timezone(student.timezone)).date()
        try:
            lesson_date = local_today + timedelta(days=profile.notification_homework)
            range_start, range_end = local_day_bounds(lesson_date, student.timezone)
        except OverflowError:
            continue
        except ValidationError:
            logger.warning("Skipped homework reminders for an invalid local date")
            continue
        targets[student.id] = (
            student,
            local_today,
            lesson_date,
            scheduled_at,
            range_start,
            range_end,
        )

    if not targets:
        return

    candidates: dict[uuid.UUID, list[Homework]] = defaultdict(list)
    target_items = list(targets.items())
    for offset in range(0, len(target_items), TARGET_BATCH_SIZE):
        batch = target_items[offset : offset + TARGET_BATCH_SIZE]
        date_filters = [
            and_(
                Homework.student_id == student_id,
                Homework.lesson_occurrence_start >= to_storage_utc(target[4]),
                Homework.lesson_occurrence_start < to_storage_utc(target[5]),
            )
            for student_id, target in batch
        ]
        rows = await session.scalars(
            select(Homework)
            .where(
                or_(*date_filters),
                Homework.status == "INCOMPLETE",
                Homework.deleted_at.is_(None),
                Homework.cleanup_pending.is_(False),
                Homework.lesson_kind.in_(("EVENT", "RECURRENT_EVENT")),
                Homework.lesson_source_id.is_not(None),
                Homework.lesson_occurrence_start.is_not(None),
            )
            .order_by(
                Homework.student_id, Homework.lesson_occurrence_start, Homework.id
            )
        )
        for homework in rows:
            candidates[homework.student_id].append(homework)

    intents: list[dict[str, object]] = []
    for student_id, homework_items in candidates.items():
        target = targets[student_id]
        student, due_date, lesson_date, scheduled_at, _, _ = target
        try:
            schedule = await schedule_service.get_schedule(
                session, student, SchedulePeriod.DAY, lesson_date, timestamp
            )
        except BadRequest, Conflict, Forbidden, ValidationError:
            logger.info("Skipped homework reminder for unavailable lesson schedule")
            continue
        for homework in homework_items:
            if not _has_exact_active_lesson(schedule.days[0].items, homework):
                continue
            if (
                homework.lesson_source_id is None
                or homework.lesson_occurrence_start is None
            ):
                continue
            occurrence_source_type = (
                "event" if homework.lesson_kind == "EVENT" else "recurrent_event"
            )
            occurrence_start = normalize_utc(homework.lesson_occurrence_start)
            intents.append(
                {
                    "id": uuid.uuid4(),
                    "recipient_id": student_id,
                    "notification_type": NotificationType.HOMEWORK_REMINDER.value,
                    "dedupe_key": (
                        f"homework-reminder:{student_id}:{homework.id}:"
                        f"{occurrence_source_type}:{homework.lesson_source_id}:"
                        f"{occurrence_start.isoformat()}"
                    ),
                    "source_id": homework.id,
                    "source_date": due_date,
                    "occurrence_source_type": occurrence_source_type,
                    "occurrence_source_id": homework.lesson_source_id,
                    "occurrence_start_at": occurrence_start,
                    "scheduled_at": scheduled_at,
                    "available_at": scheduled_at,
                    "expires_at": scheduled_at + REMINDER_CATCH_UP,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
    await insert_notification_intents(session, intents)


def _homework_reminder_due_at(
    student: User,
    profile: StudentProfile,
    now: datetime,
) -> datetime | None:
    if (
        student.notification_lessons_today is None
        or profile.notification_homework is None
    ):
        return None
    local_today = now.astimezone(get_timezone(student.timezone)).date()
    minute = student.notification_lessons_today
    local_due = resolve_local_datetime(
        datetime.combine(local_today, time(minute // 60, minute % 60)),
        student.timezone,
    )
    scheduled_at = normalize_utc(local_due)
    if (
        now < scheduled_at
        or now >= scheduled_at + REMINDER_CATCH_UP
        or normalize_utc(student.notification_schedule_updated_at) > scheduled_at
        or normalize_utc(profile.notification_homework_updated_at) > scheduled_at
    ):
        return None
    return scheduled_at


def _has_exact_active_lesson(items: list[ScheduleItem], homework: Homework) -> bool:
    if (
        homework.lesson_kind not in {"EVENT", "RECURRENT_EVENT"}
        or homework.lesson_source_id is None
        or homework.lesson_occurrence_start is None
    ):
        return False
    expected_source_type = (
        "event" if homework.lesson_kind == "EVENT" else "recurrent_event"
    )
    expected_start = normalize_utc(homework.lesson_occurrence_start)
    return any(
        item.event_type == EventType.LESSON
        and item.source_kind.value == expected_source_type
        and item.source_id == homework.lesson_source_id
        and normalize_utc(item.start) == expected_start
        for item in items
    )
