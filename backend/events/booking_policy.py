import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import TeacherProfile, User
from backend.constants import EventType
from backend.events.occurrences import Occurrence
from backend.events.schemas import (
    EventCreate,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventUpdate,
)
from backend.events.time_utils import get_timezone, local_day_bounds, normalize_utc
from backend.exceptions import ValidationError


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


def lesson_policy_range(
    candidates: Sequence[Occurrence], timezone_name: str
) -> tuple[datetime, datetime] | None:
    if not candidates:
        return None
    zone = get_timezone(timezone_name)
    local_dates = [
        normalize_utc(item.start).astimezone(zone).date() for item in candidates
    ]
    range_start, _ = local_day_bounds(min(local_dates), timezone_name)
    _, range_end = local_day_bounds(max(local_dates), timezone_name)
    return range_start, range_end


def validate_lesson_work_window(
    occurrences: Sequence[Occurrence],
    profile: TeacherBookingPreferences,
    timezone_name: str,
) -> None:
    if profile.work_start is None or profile.work_end is None:
        raise ValidationError("Teacher work hours are not configured")

    zone = get_timezone(timezone_name)
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


def validate_lesson_limits(
    candidates: Sequence[Occurrence],
    existing: Sequence[Occurrence],
    profile: TeacherBookingPreferences,
    timezone_name: str,
) -> None:
    if not candidates:
        return
    zone = get_timezone(timezone_name)
    lessons_by_day: dict[date, list[Occurrence]] = defaultdict(list)
    candidate_days: set[date] = set()
    for occurrence in (*existing, *candidates):
        if occurrence.source.event_type != EventType.LESSON:
            continue
        day = normalize_utc(occurrence.start).astimezone(zone).date()
        lessons_by_day[day].append(occurrence)
    for candidate in candidates:
        candidate_days.add(normalize_utc(candidate.start).astimezone(zone).date())

    break_duration = timedelta(minutes=profile.between_lessons_break)
    for day in candidate_days:
        lessons = sorted(
            lessons_by_day[day],
            key=lambda occurrence: (
                normalize_utc(occurrence.start),
                normalize_utc(occurrence.end),
            ),
        )
        if len(lessons) > profile.max_lessons_per_day:
            raise ValidationError(
                f"Teacher's daily lesson limit of {profile.max_lessons_per_day} "
                "would be exceeded"
            )

        longest_chain = chain_length = 0
        previous: Occurrence | None = None
        for lesson in lessons:
            if (
                previous is None
                or normalize_utc(lesson.start) - normalize_utc(previous.end)
                > break_duration
            ):
                chain_length = 1
            else:
                chain_length += 1
            longest_chain = max(longest_chain, chain_length)
            previous = lesson
        if longest_chain > profile.max_consecutive_lessons:
            raise ValidationError(
                "Teacher's consecutive lesson limit of "
                f"{profile.max_consecutive_lessons} would be exceeded"
            )
