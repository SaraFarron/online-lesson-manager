"""Advisory availability reads built on the same rules as schedule writes.

Results are not reservations: a concurrent write can still conflict, and the
write path stays authoritative. These reads deliberately do not take the
per-teacher mutation guard.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events.booking_policy import (
    TeacherBookingPreferences,
    get_teacher_preferences,
    is_within_work_window,
)
from backend.events.conflicts import (
    exclude_source_occurrences,
    is_one_off_slot_acceptable,
)
from backend.events.management.schemas import (
    AvailableEndTimes,
    AvailableStartTimes,
    ResolvedLocalInstant,
)
from backend.events.models import Event, RecurrentEvent
from backend.events.occurrences import Occurrence, OccurrenceSourceKind
from backend.events.range_service import load_raw_occurrences_for_validation
from backend.events.schemas import EventCreate
from backend.events.time_utils import (
    get_timezone,
    local_day_bounds,
    normalize_utc,
    resolve_local_datetime,
)
from backend.events.utils import (
    assert_can_assign_user,
    assert_owns_event,
    assert_teacher_actor,
    resolve_teacher_id,
)
from backend.exceptions import Forbidden, ValidationError

SLOT_STEPS = (5, 15)
MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class _Context:
    teacher_id: uuid.UUID
    user_id: uuid.UUID
    event_type: EventType
    profile: TeacherBookingPreferences
    teacher_timezone: str
    post_break_minutes: int
    excluded_id: uuid.UUID | None
    excluded_kind: OccurrenceSourceKind | None
    excluded_start: datetime | None

    @property
    def lesson_preferences(self) -> TeacherBookingPreferences | None:
        return self.profile if self.event_type == EventType.LESSON else None

    @property
    def policy_timezone(self) -> str | None:
        return self.teacher_timezone if self.event_type == EventType.LESSON else None


async def list_available_start_times(
    session: AsyncSession,
    actor: User,
    *,
    day: date,
    event_type: EventType,
    duration_minutes: int | None,
    step_minutes: int,
    subject_id: uuid.UUID | None,
    exclude_event_id: uuid.UUID | None,
    exclude_occurrence_start: datetime | None,
    now: datetime,
) -> AvailableStartTimes:
    _validate_step(step_minutes)
    context = await _build_context(
        session,
        actor,
        event_type,
        subject_id,
        exclude_event_id,
        exclude_occurrence_start,
    )
    if event_type == EventType.LESSON:
        duration = _lesson_duration(actor, context.profile, duration_minutes)
    else:
        # The smallest possible break: one grid step must fit from the start.
        duration = step_minutes

    viewer_timezone = actor.timezone
    candidates = _candidate_intervals(
        day, viewer_timezone, step_minutes, timedelta(minutes=duration), now
    )
    if event_type == EventType.BREAK:
        candidates = [
            (local, start, end)
            for local, start, end in candidates
            if is_within_work_window(
                start, end, context.profile, context.teacher_timezone
            )
        ]
    if not candidates:
        return _start_times(viewer_timezone, day, step_minutes, duration, [])

    day_start, day_end = local_day_bounds(day, viewer_timezone)
    range_start = day_start
    range_end = candidates[-1][2] + timedelta(minutes=context.post_break_minutes)
    if event_type == EventType.LESSON:
        policy_start, _ = local_day_bounds(
            candidates[0][1].astimezone(get_timezone(context.teacher_timezone)).date(),
            context.teacher_timezone,
        )
        _, policy_end = local_day_bounds(
            candidates[-1][1].astimezone(get_timezone(context.teacher_timezone)).date(),
            context.teacher_timezone,
        )
        range_start = min(range_start, policy_start)
        range_end = max(range_end, policy_end)
    range_end = max(range_end, day_end)
    existing = await _load_existing(session, context, range_start, range_end)

    slots = [
        ResolvedLocalInstant.from_local(local, start)
        for local, start, end in candidates
        if _is_acceptable(context, start, end, existing)
    ]
    return _start_times(viewer_timezone, day, step_minutes, duration, slots)


async def list_available_break_end_times(
    session: AsyncSession,
    actor: User,
    *,
    start: datetime,
    step_minutes: int,
    exclude_event_id: uuid.UUID | None,
    exclude_occurrence_start: datetime | None,
) -> AvailableEndTimes:
    _validate_step(step_minutes)
    context = await _build_context(
        session,
        actor,
        EventType.BREAK,
        None,
        exclude_event_id,
        exclude_occurrence_start,
    )
    viewer_zone = get_timezone(actor.timezone)
    start_utc = normalize_utc(start)
    local_start = start_utc.astimezone(viewer_zone)
    if (
        local_start.second
        or local_start.microsecond
        or (local_start.hour * 60 + local_start.minute) % step_minutes
    ):
        raise ValidationError("Break start must be aligned to the requested step")

    _, day_end = local_day_bounds(local_start.date(), actor.timezone)
    existing = await _load_existing(session, context, start_utc, day_end)

    ends: list[ResolvedLocalInstant] = []
    step = timedelta(minutes=step_minutes)
    end = start_utc + step
    while end <= day_end and is_within_work_window(
        start_utc, end, context.profile, context.teacher_timezone
    ):
        if not _is_acceptable(context, start_utc, end, existing):
            break
        ends.append(ResolvedLocalInstant.from_local(end.astimezone(viewer_zone), end))
        end += step
    return AvailableEndTimes(
        timezone=actor.timezone,
        start=ResolvedLocalInstant.from_local(local_start, start_utc),
        step_minutes=step_minutes,
        ends=ends,
    )


def _validate_step(step_minutes: int) -> None:
    if step_minutes not in SLOT_STEPS:
        raise ValidationError("Step must be 5 or 15 minutes")


def _lesson_duration(
    actor: User, profile: TeacherBookingPreferences, requested: int | None
) -> int:
    if requested is None:
        return profile.lesson_length
    if requested != profile.lesson_length and actor.role != Roles.TEACHER:
        raise ValidationError(
            "Students must use the teacher's configured lesson duration"
        )
    return requested


def _start_times(
    timezone: str,
    day: date,
    step_minutes: int,
    duration: int,
    slots: list[ResolvedLocalInstant],
) -> AvailableStartTimes:
    return AvailableStartTimes(
        timezone=timezone,
        day=day,
        step_minutes=step_minutes,
        duration_minutes=duration,
        slots=slots,
    )


async def _build_context(
    session: AsyncSession,
    actor: User,
    event_type: EventType,
    subject_id: uuid.UUID | None,
    exclude_event_id: uuid.UUID | None,
    exclude_occurrence_start: datetime | None,
) -> _Context:
    if actor.role not in {Roles.TEACHER, Roles.STUDENT}:
        raise Forbidden("Only teachers and students can read availability")
    if event_type not in {EventType.LESSON, EventType.BREAK}:
        raise ValidationError("Availability is only available for lessons and breaks")
    if event_type == EventType.BREAK:
        assert_teacher_actor(actor)
    teacher_id = resolve_teacher_id(actor)
    user_id = subject_id or actor.id
    if event_type == EventType.BREAK and user_id != actor.id:
        raise ValidationError("Breaks belong to the teacher")
    await assert_can_assign_user(session, actor, user_id)
    profile, teacher_timezone = await get_teacher_preferences(session, teacher_id)

    post_break_minutes = (
        profile.between_lessons_break if event_type == EventType.LESSON else 0
    )
    excluded_kind: OccurrenceSourceKind | None = None
    if exclude_event_id is not None:
        excluded, excluded_kind = await _load_excluded(
            session, actor, exclude_event_id, event_type
        )
        if event_type == EventType.LESSON:
            post_break_minutes = excluded.post_break_minutes
    return _Context(
        teacher_id=teacher_id,
        user_id=user_id,
        event_type=event_type,
        profile=profile,
        teacher_timezone=teacher_timezone,
        post_break_minutes=post_break_minutes,
        excluded_id=exclude_event_id,
        excluded_kind=excluded_kind,
        excluded_start=exclude_occurrence_start,
    )


async def _load_excluded(
    session: AsyncSession,
    actor: User,
    event_id: uuid.UUID,
    event_type: EventType,
) -> tuple[Event | RecurrentEvent, OccurrenceSourceKind]:
    event: Event | RecurrentEvent | None = await session.get(Event, event_id)
    kind = OccurrenceSourceKind.EVENT
    if event is None:
        event = await session.get(RecurrentEvent, event_id)
        kind = OccurrenceSourceKind.RECURRENT_EVENT
    if event is None or event.event_type != event_type.value:
        raise ValidationError("Event not found")
    assert_owns_event(actor, event)
    return event, kind


def _candidate_intervals(
    day: date,
    timezone_name: str,
    step_minutes: int,
    duration: timedelta,
    now: datetime,
) -> list[tuple[datetime, datetime, datetime]]:
    """Return (local, start UTC, end UTC) for each real, future clock-aligned time."""
    midnight = datetime.combine(day, time.min)
    reference = normalize_utc(now)
    candidates: list[tuple[datetime, datetime, datetime]] = []
    for minute in range(0, MINUTES_PER_DAY, step_minutes):
        naive = midnight + timedelta(minutes=minute)
        local = resolve_local_datetime(naive, timezone_name)
        if local.replace(tzinfo=None) != naive:
            continue
        start = local.astimezone(UTC)
        if start <= reference:
            continue
        candidates.append((local, start, start + duration))
    return candidates


async def _load_existing(
    session: AsyncSession,
    context: _Context,
    range_start: datetime,
    range_end: datetime,
) -> list[Occurrence]:
    existing = await load_raw_occurrences_for_validation(
        session,
        context.teacher_id,
        range_start,
        range_end,
        include_automatic_breaks=True,
    )
    if context.excluded_id is not None and context.excluded_kind is not None:
        existing = exclude_source_occurrences(
            existing,
            context.excluded_id,
            context.excluded_kind,
            context.excluded_start,
        )
    return existing


def _is_acceptable(
    context: _Context,
    start: datetime,
    end: datetime,
    existing: list[Occurrence],
) -> bool:
    data = EventCreate(
        start=start,
        end=end,
        user_id=context.user_id,
        event_type=context.event_type,
    )
    return is_one_off_slot_acceptable(
        data,
        context.teacher_id,
        context.event_type,
        existing,
        lesson_preferences=context.lesson_preferences,
        policy_timezone=context.policy_timezone,
        post_break_minutes=context.post_break_minutes,
    )
