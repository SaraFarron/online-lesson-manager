from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from uuid import UUID

from backend.constants import EventType
from backend.events.occurrences import Occurrence, sort_occurrences
from backend.events.time_utils import (
    get_timezone,
    local_day_bounds,
    local_week_bounds,
    normalize_utc,
)
from backend.schedule.schemas import (
    ScheduleDay,
    ScheduleEventStatus,
    ScheduleItem,
    SchedulePeriod,
    ScheduleResponse,
    TeacherLessonScheduleItem,
)

ScheduleItemOutput = ScheduleItem | TeacherLessonScheduleItem


def shape_schedule(
    period: SchedulePeriod,
    requested_date: date,
    timezone_name: str,
    generated_at: datetime,
    occurrences: Sequence[Occurrence],
    *,
    subject_labels: Mapping[UUID, str] | None = None,
) -> ScheduleResponse:
    """Build a safe viewer-local snapshot from already-authorized occurrences."""
    zone = get_timezone(timezone_name)
    now = normalize_utc(generated_at)

    if period == SchedulePeriod.DAY:
        dates: tuple[date, ...] = (requested_date,)
        range_start, range_end = local_day_bounds(requested_date, timezone_name)
    else:
        range_start, range_end = local_week_bounds(requested_date, timezone_name)
        monday = requested_date - timedelta(days=requested_date.weekday())
        dates = tuple(monday + timedelta(days=offset) for offset in range(7))

    days_by_date: dict[date, list[ScheduleItemOutput]] = {day: [] for day in dates}
    for occurrence in sort_occurrences(occurrences):
        start = normalize_utc(occurrence.start)
        end = normalize_utc(occurrence.end)
        local_start_date = start.astimezone(zone).date()
        if local_start_date not in days_by_date:
            continue

        if end <= now:
            status = ScheduleEventStatus.PAST
        elif start <= now:
            status = ScheduleEventStatus.ONGOING
        else:
            status = ScheduleEventStatus.UPCOMING

        subject_label = (
            subject_labels.get(occurrence.source.user_id)
            if subject_labels is not None
            and occurrence.source.event_type == EventType.LESSON
            else None
        )
        if subject_label is None:
            item: ScheduleItemOutput = ScheduleItem(
                source_kind=occurrence.source.kind,
                source_id=occurrence.source.id,
                event_type=occurrence.source.event_type,
                start=start,
                end=end,
                status=status,
            )
        else:
            item = TeacherLessonScheduleItem(
                source_kind=occurrence.source.kind,
                source_id=occurrence.source.id,
                event_type=occurrence.source.event_type,
                start=start,
                end=end,
                status=status,
                subject_label=subject_label,
            )
        days_by_date[local_start_date].append(item)

    return ScheduleResponse(
        period=period,
        timezone=timezone_name,
        generated_at=now,
        range_start=range_start,
        range_end=range_end,
        days=[ScheduleDay(date=day, items=days_by_date[day]) for day in dates],
    )
