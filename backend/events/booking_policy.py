import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import TeacherProfile, User
from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.occurrences import (
    Occurrence,
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    expand_recurrence,
)
from backend.events.schemas import (
    EventCreate,
    EventUpdate,
    EventWrite,
    RecurrentEventCreate,
    RecurrentEventUpdate,
    RecurrentEventWrite,
)
from backend.events.time_utils import get_timezone, normalize_utc
from backend.exceptions import ValidationError

MAX_RECURRENT_HORIZON_YEARS = 10


@dataclass(frozen=True)
class TeacherBookingPreferences:
    work_start: time = time(9)
    work_end: time = time(18)
    working_days: tuple[int, ...] = (1, 2, 3, 4, 5)
    lesson_length: int = 60
    between_lessons_break: int = 5
    max_consecutive_lessons: int = 4
    max_lessons_per_day: int = 6


async def get_teacher_preferences(
    session: AsyncSession, teacher_id: uuid.UUID
) -> tuple[TeacherBookingPreferences, str]:
    result = await session.execute(
        select(User.timezone, TeacherProfile)
        .outerjoin(TeacherProfile, TeacherProfile.teacher_id == User.id)
        .where(User.id == teacher_id)
    )
    row = result.one_or_none()
    if row is None:
        raise ValidationError(f"Teacher with ID {teacher_id} does not exist")

    profile = row[1]
    if profile is None:
        return TeacherBookingPreferences(), row[0]
    return (
        TeacherBookingPreferences(
            work_start=profile.work_start,
            work_end=profile.work_end,
            working_days=tuple(profile.working_days or ()),
            lesson_length=profile.lesson_length,
            between_lessons_break=profile.between_lessons_break,
            max_consecutive_lessons=profile.max_consecutive_lessons,
            max_lessons_per_day=profile.max_lessons_per_day,
        ),
        row[0],
    )


async def prepare_lesson_write[
    LessonWriteT: EventCreate
    | EventUpdate
    | RecurrentEventCreate
    | RecurrentEventUpdate
](
    session: AsyncSession,
    data: LessonWriteT,
    teacher_id: uuid.UUID,
    *,
    actor_is_teacher: bool,
) -> tuple[LessonWriteT, TeacherBookingPreferences, str]:
    profile, teacher_timezone = await get_teacher_preferences(session, teacher_id)
    end = data.end
    if end is None:
        try:
            end = normalize_utc(data.start) + timedelta(minutes=profile.lesson_length)
        except (OverflowError, ValueError) as exc:
            raise ValidationError(
                "Default lesson duration is outside the supported range"
            ) from exc
        data.end = end

    duration = normalize_utc(end) - normalize_utc(data.start)
    configured_duration = timedelta(minutes=profile.lesson_length)
    if duration != configured_duration and not actor_is_teacher:
        raise ValidationError(
            "Students must use the teacher's configured lesson duration"
        )
    return data, profile, teacher_timezone


def validate_lesson_work_window(
    data: EventWrite | RecurrentEventWrite,
    teacher_id: uuid.UUID,
    profile: TeacherBookingPreferences,
    teacher_timezone: str,
    *,
    recurrence_timezone: str | None = None,
    recurrence_mode: str | None = None,
    canceled_starts: Sequence[datetime] = (),
) -> None:
    if data.end is None:
        raise ValidationError("Lesson end time is required")
    if profile.work_start is None or profile.work_end is None:
        raise ValidationError("Teacher work hours are not configured")

    mode = recurrence_mode or RecurrenceMode.TEACHER_LOCAL.value
    timezone_name = (
        recurrence_timezone
        if isinstance(data, RecurrentEventWrite)
        and mode == RecurrenceMode.TEACHER_LOCAL
        and recurrence_timezone is not None
        else teacher_timezone
    )
    zone = get_timezone(timezone_name)
    occurrences = _candidate_occurrences(
        data,
        teacher_id,
        recurrence_timezone=recurrence_timezone or teacher_timezone,
        recurrence_mode=mode,
        canceled_starts=canceled_starts,
    )
    working_days = profile.working_days
    if (
        not isinstance(working_days, Sequence)
        or not working_days
        or any(type(day) is not int or not 1 <= day <= 7 for day in working_days)
    ):
        raise ValidationError("Teacher working days are not configured")

    for occurrence in occurrences:
        local_start = normalize_utc(occurrence.start).astimezone(zone)
        local_end = normalize_utc(occurrence.end).astimezone(zone)
        if local_start.date().isoweekday() not in working_days:
            raise ValidationError("Lesson starts on a non-working day")
        if local_start.time().replace(tzinfo=None) < profile.work_start:
            raise ValidationError("Lesson starts before the teacher's work hours")
        if local_end.date() != local_start.date():
            raise ValidationError("Lesson cannot cross local midnight")
        if local_end.time().replace(tzinfo=None) > profile.work_end:
            raise ValidationError("Lesson ends after the teacher's work hours")


def _candidate_occurrences(
    data: EventWrite | RecurrentEventWrite,
    teacher_id: uuid.UUID,
    *,
    recurrence_timezone: str,
    recurrence_mode: str,
    canceled_starts: Sequence[datetime],
) -> list[Occurrence]:
    if data.end is None:
        raise ValidationError("Lesson end time is required")

    source = OccurrenceSource(
        kind=(
            OccurrenceSourceKind.RECURRENT_EVENT
            if isinstance(data, RecurrentEventWrite)
            else OccurrenceSourceKind.EVENT
        ),
        id=uuid.UUID(int=0),
        teacher_id=teacher_id,
        user_id=data.user_id,
        event_type=EventType.LESSON,
    )
    start = normalize_utc(data.start)
    end = normalize_utc(data.end)
    if not isinstance(data, RecurrentEventWrite):
        return [Occurrence(source=source, start=start, end=end)]

    cutoff = normalize_utc(data.interval_end)
    target_year = start.year + MAX_RECURRENT_HORIZON_YEARS
    if target_year > 9999:
        raise ValidationError("Recurrence start is outside the supported range")
    try:
        max_cutoff = start.replace(year=target_year)
    except ValueError as exc:
        if start.month != 2 or start.day != 29:
            raise ValidationError(
                "Recurrence start is outside the supported range"
            ) from exc
        max_cutoff = start.replace(year=target_year, day=28)
    if cutoff > max_cutoff:
        raise ValidationError("Recurrence duration cannot exceed 10 years")
    try:
        range_end = cutoff + (end - start)
    except OverflowError as exc:
        raise ValidationError(
            "Recurrence policy horizon is outside the supported range"
        ) from exc

    series = RecurrenceSeries(
        source=source,
        start=start,
        end=end,
        interval=data.interval,
        interval_end=cutoff,
        recurrence_mode=recurrence_mode,
        recurrence_timezone=(
            recurrence_timezone
            if recurrence_mode == RecurrenceMode.TEACHER_LOCAL
            else None
        ),
    )
    canceled = {normalize_utc(value) for value in canceled_starts}
    return [
        occurrence
        for occurrence in expand_recurrence(series, start, range_end)
        if occurrence.start not in canceled
    ]
