import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events.time_utils import (
    get_timezone,
    local_day_bounds,
    normalize_utc,
    resolve_local_datetime,
)
from backend.notifications.constants import NotificationType
from backend.notifications.models import NotificationIntent
from backend.schedule import service as schedule_service
from backend.schedule.schemas import ScheduleItem, SchedulePeriod, ScheduleResponse

REMINDER_CATCH_UP = timedelta(minutes=15)


async def produce_schedule_notifications(
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
    users = await session.scalars(
        select(User)
        .where(
            User.is_active.is_(True),
            User.telegram_private_chat_id.is_not(None),
            or_(
                User.role == Roles.TEACHER,
                and_(User.role == Roles.STUDENT, has_active_teacher),
            ),
        )
        .options(selectinload(User.student_profile))
        .order_by(User.id)
    )

    intents: list[dict[str, object]] = []
    for user in users:
        local_today = timestamp.astimezone(get_timezone(user.timezone)).date()
        digest_at = _daily_digest_due_at(user, local_today, timestamp)
        schedule_dates = {local_today} if digest_at is not None else set()
        lead_minutes: int | None = None
        if user.role == Roles.STUDENT and user.student_profile is not None:
            lead_minutes = user.student_profile.notification_lesson
            if lead_minutes is not None:
                schedule_dates.add(local_today)
                schedule_dates.add(local_today + timedelta(days=1))
        schedules = {
            schedule_date: await schedule_service.get_schedule(
                session,
                user,
                SchedulePeriod.DAY,
                schedule_date,
                timestamp,
            )
            for schedule_date in sorted(schedule_dates)
        }
        if digest_at is not None:
            digest = _daily_digest_intent(
                user, local_today, timestamp, digest_at, schedules.get(local_today)
            )
            if digest is not None:
                intents.append(digest)
        if lead_minutes is None:
            continue
        for schedule in schedules.values():
            for item in schedule.days[0].items:
                if item.event_type != EventType.LESSON:
                    continue
                reminder = _lesson_reminder_intent(
                    user,
                    item,
                    lead_minutes,
                    timestamp,
                )
                if reminder is not None:
                    intents.append(reminder)

    if not intents:
        return
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        await session.execute(
            postgresql_insert(NotificationIntent)
            .values(intents)
            .on_conflict_do_nothing(index_elements=[NotificationIntent.dedupe_key])
        )
    elif dialect == "sqlite":
        await session.execute(
            sqlite_insert(NotificationIntent)
            .values(intents)
            .on_conflict_do_nothing(index_elements=[NotificationIntent.dedupe_key])
        )
    else:
        raise RuntimeError(f"Unsupported notification producer database: {dialect}")


def _daily_digest_due_at(
    user: User, schedule_date: date, now: datetime
) -> datetime | None:
    configured_minute = user.notification_lessons_today
    if configured_minute is None:
        return None
    local_due = resolve_local_datetime(
        datetime.combine(
            schedule_date,
            time(configured_minute // 60, configured_minute % 60),
        ),
        user.timezone,
    )
    scheduled_at = normalize_utc(local_due)
    if now < scheduled_at or not _was_enabled_at(
        user.notification_schedule_updated_at, scheduled_at
    ):
        return None
    return scheduled_at


def _daily_digest_intent(
    user: User,
    schedule_date: date,
    now: datetime,
    scheduled_at: datetime,
    schedule: ScheduleResponse | None,
) -> dict[str, object] | None:
    if schedule is None or not any(
        item.event_type == EventType.LESSON for item in schedule.days[0].items
    ):
        return None
    _, expires_at = local_day_bounds(schedule_date, user.timezone)
    return {
        "id": uuid.uuid4(),
        "recipient_id": user.id,
        "notification_type": NotificationType.DAILY_DIGEST.value,
        "dedupe_key": f"digest:{user.id}:{schedule_date.isoformat()}",
        "source_date": schedule_date,
        "scheduled_at": scheduled_at,
        "available_at": scheduled_at,
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
    }


def _lesson_reminder_intent(
    user: User,
    item: ScheduleItem,
    lead_minutes: int,
    now: datetime,
) -> dict[str, object] | None:
    scheduled_at = normalize_utc(item.start) - timedelta(minutes=lead_minutes)
    expires_at = scheduled_at + REMINDER_CATCH_UP
    if (
        now < scheduled_at
        or now >= expires_at
        or normalize_utc(item.start) <= now
        or normalize_utc(user.notification_schedule_updated_at) > scheduled_at
        or user.student_profile is None
        or normalize_utc(user.student_profile.notification_lesson_updated_at)
        > scheduled_at
    ):
        return None
    source_kind = item.source_kind.value
    start = normalize_utc(item.start)
    return {
        "id": uuid.uuid4(),
        "recipient_id": user.id,
        "notification_type": NotificationType.LESSON_REMINDER.value,
        "dedupe_key": (
            f"lesson:{user.id}:{source_kind}:{item.source_id}:{start.isoformat()}"
        ),
        "occurrence_source_type": source_kind,
        "occurrence_source_id": item.source_id,
        "occurrence_start_at": start,
        "scheduled_at": scheduled_at,
        "available_at": scheduled_at,
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
    }


def _was_enabled_at(updated_at: datetime, scheduled_at: datetime) -> bool:
    return normalize_utc(updated_at) <= scheduled_at
