from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events.occurrences import OccurrenceSourceKind
from backend.events.time_utils import normalize_utc, to_storage_utc
from backend.homework.models import Homework
from backend.homework.schemas import HomeworkStatus
from backend.schedule import service as schedule_service
from backend.schedule.schemas import SchedulePeriod, ScheduleResponse

type LessonIdentity = tuple[OccurrenceSourceKind, UUID, datetime]


@dataclass(frozen=True)
class DailyDigestContext:
    schedule: ScheduleResponse
    homework_status_by_lesson: dict[LessonIdentity, tuple[HomeworkStatus, ...]]


async def get_daily_digest_context(
    session: AsyncSession,
    viewer: User,
    schedule_date: date,
    now: datetime,
) -> DailyDigestContext:
    schedule = await schedule_service.get_schedule(
        session, viewer, SchedulePeriod.DAY, schedule_date, now
    )
    homework_status_by_lesson: dict[LessonIdentity, tuple[HomeworkStatus, ...]] = {}
    if viewer.role == Roles.STUDENT:
        lesson_identities = {
            (
                item.source_kind,
                item.source_id,
                normalize_utc(item.start),
            )
            for item in schedule.days[0].items
            if item.event_type == EventType.LESSON
        }
        source_ids = {source_id for _, source_id, _ in lesson_identities}
        if source_ids:
            rows = await session.execute(
                select(
                    Homework.lesson_kind,
                    Homework.lesson_source_id,
                    Homework.lesson_occurrence_start,
                    Homework.status,
                )
                .where(
                    Homework.student_id == viewer.id,
                    Homework.deleted_at.is_(None),
                    Homework.cleanup_pending.is_(False),
                    Homework.lesson_source_id.in_(source_ids),
                    Homework.lesson_occurrence_start
                    >= to_storage_utc(schedule.range_start),
                    Homework.lesson_occurrence_start
                    < to_storage_utc(schedule.range_end),
                )
                .order_by(Homework.created_at, Homework.id)
            )
            grouped: dict[LessonIdentity, list[HomeworkStatus]] = {}
            for lesson_kind, source_id, occurrence_start, status in rows.all():
                if source_id is None or occurrence_start is None:
                    continue
                source_kind = (
                    OccurrenceSourceKind.EVENT
                    if lesson_kind == "EVENT"
                    else OccurrenceSourceKind.RECURRENT_EVENT
                )
                identity = (
                    source_kind,
                    source_id,
                    normalize_utc(occurrence_start),
                )
                if identity in lesson_identities:
                    grouped.setdefault(identity, []).append(HomeworkStatus(status))
            homework_status_by_lesson = {
                identity: tuple(statuses) for identity, statuses in grouped.items()
            }
    return DailyDigestContext(schedule, homework_status_by_lesson)
