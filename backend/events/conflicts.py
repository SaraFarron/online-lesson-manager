import hashlib
import json
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.service import get_user_by_id
from backend.constants import EventType
from backend.events.booking_policy import (
    TeacherBookingPreferences,
    get_teacher_preferences,
    lesson_policy_range,
    validate_lesson_limits,
    validate_lesson_work_window,
)
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentEvent
from backend.events.occurrences import (
    MAX_OCCURRENCES_PER_QUERY,
    Occurrence,
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    derive_automatic_break,
    expand_recurrence,
    sort_occurrences,
)
from backend.events.range_service import load_raw_occurrences_for_validation
from backend.events.schemas import (
    EventCreate,
    EventUpdate,
    EventWrite,
    RecurrentEventCreate,
    RecurrentEventUpdate,
    RecurrentEventWrite,
)
from backend.events.time_utils import (
    intervals_overlap,
    normalize_utc,
)
from backend.exceptions import Conflict, ValidationError

MAX_RECURRENT_HORIZON_YEARS = 10


async def validate_schedule_change(
    session: AsyncSession,
    data: EventWrite | RecurrentEventWrite,
    teacher_id: UUID,
    *,
    expected_event_type: EventType | None = None,
    exclude_event_id: UUID | None = None,
    recurrence_timezone: str | None = None,
    recurrence_mode: RecurrenceMode | str | None = None,
    canceled_starts: Sequence[datetime] = (),
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    lesson_preferences: TeacherBookingPreferences | None = None,
    teacher_timezone: str | None = None,
    post_break_minutes: int | None = None,
) -> list[Occurrence]:
    event_type = await _event_type(session, data, expected_event_type, exclude_event_id)
    if event_type == EventType.LESSON and (
        lesson_preferences is None or teacher_timezone is None
    ):
        lesson_preferences, teacher_timezone = await get_teacher_preferences(
            session, teacher_id
        )
    candidate_occurrences, range_end, candidate_count = await _candidate_occurrences(
        session,
        data,
        teacher_id,
        event_type=event_type,
        recurrence_timezone=recurrence_timezone,
        recurrence_mode=recurrence_mode,
        canceled_starts=canceled_starts,
        post_break_minutes=post_break_minutes,
    )
    candidate_lessons = candidate_occurrences
    candidate_breaks = (
        [
            automatic_break
            for occurrence in candidate_lessons
            if (automatic_break := derive_automatic_break(occurrence)) is not None
        ]
        if event_type == EventType.LESSON
        else []
    )
    candidate_reservations = [*candidate_lessons, *candidate_breaks]
    candidate_count += len(candidate_breaks)
    if candidate_count > MAX_OCCURRENCES_PER_QUERY:
        raise ValidationError(
            f"Occurrence query exceeds the {MAX_OCCURRENCES_PER_QUERY} occurrence limit"
        )

    if isinstance(data, RecurrentEventWrite):
        _reject_self_overlap(candidate_reservations)

    policy_timezone = teacher_timezone
    if (
        event_type == EventType.LESSON
        and isinstance(data, RecurrentEventWrite)
        and (recurrence_mode or RecurrenceMode.TEACHER_LOCAL)
        == RecurrenceMode.TEACHER_LOCAL
        and recurrence_timezone is not None
    ):
        policy_timezone = recurrence_timezone
    policy_range = (
        lesson_policy_range(candidate_lessons, policy_timezone)
        if event_type == EventType.LESSON
        and lesson_preferences is not None
        and policy_timezone is not None
        else None
    )
    query_start = normalize_utc(data.start)
    query_end = max(
        range_end,
        max(
            (occurrence.end for occurrence in candidate_reservations), default=range_end
        ),
    )
    if policy_range is not None:
        query_start = min(query_start, policy_range[0])
        query_end = max(query_end, policy_range[1])

    existing = await load_raw_occurrences_for_validation(
        session,
        teacher_id,
        query_start,
        query_end,
        max_occurrences=MAX_OCCURRENCES_PER_QUERY - candidate_count,
        include_automatic_breaks=True,
    )
    if exclude_event_id is not None:
        excluded_kind = (
            OccurrenceSourceKind.RECURRENT_EVENT
            if isinstance(data, RecurrentEventWrite)
            else OccurrenceSourceKind.EVENT
        )
        existing = [
            occurrence
            for occurrence in existing
            if not (
                (
                    occurrence.source.kind == excluded_kind
                    and occurrence.source.id == exclude_event_id
                )
                or (
                    occurrence.source.kind == OccurrenceSourceKind.AUTOMATIC_BREAK
                    and occurrence.source.parent_kind == excluded_kind
                    and occurrence.source.id == exclude_event_id
                )
            )
        ]

    if (
        event_type == EventType.LESSON
        and candidate_occurrences
        and lesson_preferences is not None
        and policy_timezone is not None
    ):
        validate_lesson_work_window(
            candidate_lessons, lesson_preferences, policy_timezone
        )
        validate_lesson_limits(
            candidate_lessons, existing, lesson_preferences, policy_timezone
        )

    overlaps = _find_overlaps(candidate_reservations, existing)
    if isinstance(data, EventWrite):
        vacation_check_candidates = (
            candidate_lessons
            if event_type == EventType.LESSON
            else candidate_reservations
        )
        vacation_overlaps = _find_overlaps(
            vacation_check_candidates,
            [
                occurrence
                for occurrence in existing
                if occurrence.source.event_type == EventType.VACATION
            ],
        )
        applicable_vacation_overlaps = [
            occurrence
            for occurrence in vacation_overlaps
            if _vacation_applies(occurrence, data.user_id, teacher_id)
        ]
        if applicable_vacation_overlaps:
            raise ValidationError("Event overlaps an applicable vacation")

        if event_type == EventType.VACATION:
            return []
        overlaps = [
            occurrence
            for occurrence in overlaps
            if occurrence.source.event_type != EventType.VACATION
        ]
    else:
        overlaps = [
            occurrence
            for occurrence in overlaps
            if occurrence.source.event_type != EventType.VACATION
        ]
        if event_type == EventType.VACATION:
            return []

    if event_type == EventType.BREAK:
        lesson_overlaps = [
            occurrence
            for occurrence in overlaps
            if occurrence.source.event_type == EventType.LESSON
        ]
        linked_automatic_breaks = {
            (lesson.source.kind, lesson.source.id, normalize_utc(lesson.start))
            for lesson in lesson_overlaps
        }
        blocking_overlaps = [
            occurrence
            for occurrence in overlaps
            if occurrence.source.event_type != EventType.LESSON
            and not (
                occurrence.source.kind == OccurrenceSourceKind.AUTOMATIC_BREAK
                and occurrence.source.parent_kind is not None
                and occurrence.source.parent_start is not None
                and (
                    occurrence.source.parent_kind,
                    occurrence.source.id,
                    normalize_utc(occurrence.source.parent_start),
                )
                in linked_automatic_breaks
            )
        ]
        if blocking_overlaps:
            raise ValidationError("Event overlaps an existing non-lesson event")
        if lesson_overlaps:
            token = _break_confirmation_token(
                data,
                teacher_id,
                event_type,
                exclude_event_id,
                lesson_overlaps,
            )
            if not confirm_displacement or (
                displacement_token is not None and displacement_token != token
            ):
                raise Conflict(_break_conflict_detail(lesson_overlaps, token))
        return lesson_overlaps

    if overlaps:
        raise ValidationError("Event overlaps an existing event")
    return []


async def _candidate_occurrences(
    session: AsyncSession,
    data: EventWrite | RecurrentEventWrite,
    teacher_id: UUID,
    *,
    event_type: EventType,
    recurrence_timezone: str | None,
    recurrence_mode: RecurrenceMode | str | None,
    canceled_starts: Sequence[datetime],
    post_break_minutes: int | None,
) -> tuple[list[Occurrence], datetime, int]:
    source_kind = (
        OccurrenceSourceKind.RECURRENT_EVENT
        if isinstance(data, RecurrentEventWrite)
        else OccurrenceSourceKind.EVENT
    )
    source_id = UUID(int=0)
    if event_type == EventType.LESSON:
        if post_break_minutes is None:
            raise ValidationError("Lesson break duration is required for validation")
        if type(post_break_minutes) is not int or post_break_minutes < 0:
            raise ValidationError("Lesson break duration must be a nonnegative integer")
    else:
        post_break_minutes = 0

    source = OccurrenceSource(
        kind=source_kind,
        id=source_id,
        teacher_id=teacher_id,
        user_id=data.user_id,
        event_type=event_type,
        post_break_minutes=post_break_minutes,
    )
    start = normalize_utc(data.start)
    if data.end is None:
        raise ValidationError("Event end time is required")
    end = normalize_utc(data.end)
    duration = end - start
    if duration <= timedelta(0):
        raise ValidationError("Event start time must be before end time")

    if not isinstance(data, RecurrentEventWrite):
        occurrence = Occurrence(source=source, start=start, end=end)
        return [occurrence], end, 1

    cutoff = normalize_utc(data.interval_end)
    _validate_recurrence_horizon(start, cutoff)
    mode = recurrence_mode or RecurrenceMode.TEACHER_LOCAL
    if mode == RecurrenceMode.TEACHER_LOCAL and recurrence_timezone is None:
        teacher = await get_user_by_id(session, teacher_id)
        if teacher is None:
            raise ValidationError(f"Teacher with ID {teacher_id} does not exist")
        recurrence_timezone = teacher.timezone

    series = RecurrenceSeries(
        source=source,
        start=start,
        end=end,
        interval=data.interval,
        interval_end=cutoff,
        recurrence_mode=mode,
        recurrence_timezone=recurrence_timezone,
    )
    try:
        range_end = cutoff + duration
    except OverflowError as exc:
        raise ValidationError(
            "Recurrence conflict horizon is outside the supported range"
        ) from exc
    occurrences = expand_recurrence(series, start, range_end)
    canceled = {normalize_utc(value) for value in canceled_starts}
    active_occurrences = [item for item in occurrences if item.start not in canceled]
    if event_type == EventType.LESSON and post_break_minutes:
        try:
            range_end += timedelta(minutes=post_break_minutes)
        except OverflowError as exc:
            raise ValidationError(
                "Recurrence conflict horizon is outside the supported range"
            ) from exc
    return active_occurrences, range_end, len(occurrences)


async def _event_type(
    session: AsyncSession,
    data: EventWrite | RecurrentEventWrite,
    expected_event_type: EventType | None,
    exclude_event_id: UUID | None,
) -> EventType:
    if expected_event_type is None:
        if isinstance(data, (EventCreate, RecurrentEventCreate)):
            return data.event_type
    elif isinstance(data, (EventCreate, RecurrentEventCreate)):
        if data.event_type != expected_event_type:
            raise ValidationError("Event type does not match its domain")
        return data.event_type
    elif expected_event_type is not None:
        return expected_event_type
    if exclude_event_id is None:
        raise ValidationError("Expected event type is required for updates")
    if isinstance(data, RecurrentEventUpdate):
        stored_value = await session.scalar(
            select(RecurrentEvent.event_type).where(
                RecurrentEvent.id == exclude_event_id
            )
        )
    elif isinstance(data, EventUpdate):
        stored_value = await session.scalar(
            select(Event.event_type).where(Event.id == exclude_event_id)
        )
    else:
        raise ValidationError("Expected update data")
    if stored_value is None:
        raise ValidationError("Event to update was not found")
    try:
        stored_type = EventType(stored_value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid stored event type: {stored_value}") from exc
    if expected_event_type is not None and stored_type != expected_event_type:
        raise ValidationError("Event type does not match its domain")
    return stored_type


def _validate_recurrence_horizon(start: datetime, cutoff: datetime) -> None:
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
        raise ValidationError("Recurring event cutoff cannot exceed 10 years")


def _vacation_applies(vacation: Occurrence, user_id: UUID, teacher_id: UUID) -> bool:
    return vacation.source.user_id in {teacher_id, user_id}


def _reject_self_overlap(occurrences: Sequence[Occurrence]) -> None:
    ordered = sort_occurrences(occurrences)
    index = _OccurrenceIndex(ordered)
    for occurrence in ordered:
        if any(
            other.start > occurrence.start for other in index.overlapping(occurrence)
        ):
            raise ValidationError("Recurring event overlaps itself")


def _find_overlaps(
    candidates: Sequence[Occurrence], existing: Sequence[Occurrence]
) -> list[Occurrence]:
    index = _OccurrenceIndex(existing)
    matches: dict[tuple[OccurrenceSourceKind, UUID, datetime], Occurrence] = {}
    for candidate in candidates:
        for occurrence in index.overlapping(candidate):
            matches[occurrence.identity] = occurrence
    return sort_occurrences(matches.values())


class _OccurrenceIndex:
    def __init__(self, occurrences: Sequence[Occurrence]) -> None:
        self.occurrences = sort_occurrences(occurrences)
        self.starts = tuple(item.start for item in self.occurrences)
        latest_end: datetime | None = None
        prefix_ends: list[datetime] = []
        for occurrence in self.occurrences:
            latest_end = (
                occurrence.end
                if latest_end is None
                else max(latest_end, occurrence.end)
            )
            prefix_ends.append(latest_end)
        self.prefix_ends = tuple(prefix_ends)

    def overlapping(self, candidate: Occurrence) -> list[Occurrence]:
        stop = bisect_left(self.starts, candidate.end)
        first = bisect_right(self.prefix_ends, candidate.start, 0, stop)
        return [
            occurrence
            for occurrence in self.occurrences[first:stop]
            if intervals_overlap(
                candidate.start,
                candidate.end,
                occurrence.start,
                occurrence.end,
            )
        ]


def _break_confirmation_token(
    data: EventWrite | RecurrentEventWrite,
    teacher_id: UUID,
    event_type: EventType,
    exclude_event_id: UUID | None,
    occurrences: Sequence[Occurrence],
) -> str:
    payload = {
        "teacher_id": str(teacher_id),
        "event_type": event_type.value,
        "exclude_event_id": str(exclude_event_id) if exclude_event_id else None,
        "request": data.model_dump(mode="json"),
        "affected_occurrences": [
            _affected_occurrence_data(occurrence)
            for occurrence in sorted(
                occurrences,
                key=lambda item: (
                    item.start,
                    item.end,
                    item.source.kind.value,
                    item.source.id,
                ),
            )
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _affected_occurrence_data(occurrence: Occurrence) -> dict[str, object]:
    data: dict[str, object] = {
        "source_kind": occurrence.source.kind.value,
        "source_id": str(occurrence.source.id),
        "user_id": str(occurrence.source.user_id),
        "event_type": occurrence.source.event_type.value,
        "start": normalize_utc(occurrence.start).isoformat(),
        "end": normalize_utc(occurrence.end).isoformat(),
    }
    automatic_break = derive_automatic_break(occurrence)
    if automatic_break is not None:
        data["automatic_break"] = {
            "start": automatic_break.start.isoformat(),
            "end": automatic_break.end.isoformat(),
            "duration_minutes": occurrence.source.post_break_minutes,
        }
    return data


def _break_conflict_detail(
    occurrences: Sequence[Occurrence], confirmation_token: str
) -> dict[str, object]:
    return {
        "code": "break_displacement_confirmation_required",
        "message": "Confirm cancellation of overlapping lesson occurrences",
        "confirmation_token": confirmation_token,
        "affected_occurrences": [
            _affected_occurrence_data(item) for item in occurrences
        ],
    }
