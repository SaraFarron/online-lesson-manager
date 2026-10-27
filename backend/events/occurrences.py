from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import count
from uuid import UUID
from zoneinfo import ZoneInfo

from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.time_utils import (
    get_timezone,
    intervals_overlap,
    normalize_utc,
    resolve_local_datetime,
)
from backend.exceptions import ValidationError

MAX_OCCURRENCES_PER_QUERY = 10_000
# Pad local search bounds for UTC-offset jumps and shifted nonexistent wall times.
_LOCAL_TIME_SEARCH_PADDING = timedelta(days=3)


class OccurrenceSourceKind(StrEnum):
    EVENT = "event"
    RECURRENT_EVENT = "recurrent_event"
    AUTOMATIC_BREAK = "automatic_break"


@dataclass(frozen=True, slots=True)
class OccurrenceSource:
    kind: OccurrenceSourceKind
    id: UUID
    teacher_id: UUID
    user_id: UUID
    event_type: EventType
    post_break_minutes: int = 0
    parent_kind: OccurrenceSourceKind | None = None
    parent_start: datetime | None = None


@dataclass(frozen=True, slots=True)
class Occurrence:
    source: OccurrenceSource
    start: datetime
    end: datetime

    @property
    def identity(self) -> tuple[OccurrenceSourceKind, UUID, datetime]:
        if self.source.kind == OccurrenceSourceKind.AUTOMATIC_BREAK:
            if (
                self.source.parent_kind
                not in {
                    OccurrenceSourceKind.EVENT,
                    OccurrenceSourceKind.RECURRENT_EVENT,
                }
                or self.source.parent_start is None
            ):
                raise ValidationError("Automatic break has no valid parent identity")
            identity_start = normalize_utc(self.source.parent_start)
        else:
            identity_start = self.start
        return self.source.kind, self.source.id, identity_start


@dataclass(frozen=True, slots=True)
class RecurrenceSeries:
    source: OccurrenceSource
    start: datetime
    end: datetime
    interval: int
    interval_end: datetime | None
    recurrence_mode: RecurrenceMode | str
    recurrence_timezone: str | None


def derive_automatic_break(lesson: Occurrence) -> Occurrence | None:
    """Derive a break interval with identity tied to its parent occurrence."""
    if lesson.source.event_type != EventType.LESSON:
        return None
    minutes = lesson.source.post_break_minutes
    if type(minutes) is not int or minutes < 0:
        raise ValidationError("Lesson has an invalid post-break duration")
    if minutes == 0:
        return None

    break_start = normalize_utc(lesson.end)
    try:
        break_end = break_start + timedelta(minutes=minutes)
    except OverflowError as exc:
        raise ValidationError(
            "Automatic break end is outside the supported datetime range"
        ) from exc

    if lesson.source.kind not in {
        OccurrenceSourceKind.EVENT,
        OccurrenceSourceKind.RECURRENT_EVENT,
    }:
        raise ValidationError("Automatic break parent has an invalid source kind")

    source = OccurrenceSource(
        kind=OccurrenceSourceKind.AUTOMATIC_BREAK,
        id=lesson.source.id,
        teacher_id=lesson.source.teacher_id,
        user_id=lesson.source.user_id,
        event_type=EventType.BREAK,
        parent_kind=lesson.source.kind,
        parent_start=normalize_utc(lesson.start),
    )
    return Occurrence(source=source, start=break_start, end=break_end)


def open_ended_validation_cutoff(
    start: datetime,
    recurrence_mode: RecurrenceMode | str,
    recurrence_timezone: str | None,
) -> datetime:
    """Return the exclusive one-calendar-year validation boundary for an open series."""
    mode = _recurrence_mode(recurrence_mode)
    start_utc = _as_utc(start, "series start")
    if mode == RecurrenceMode.UTC_STEP:
        if recurrence_timezone is not None:
            raise ValidationError("UTC_STEP series must not have a recurrence timezone")
        local_start = start_utc
        timezone_name = None
    else:
        timezone_name = recurrence_timezone
        if not timezone_name:
            raise ValidationError("TEACHER_LOCAL series requires a recurrence timezone")
        zone = get_timezone(timezone_name)
        local_start = start_utc.astimezone(zone).replace(tzinfo=None)

    target_year = local_start.year + 1
    if target_year > 9999:
        raise ValidationError("Recurrence start is outside the supported range")
    try:
        anniversary = local_start.replace(year=target_year)
    except ValueError as exc:
        if local_start.month != 2 or local_start.day != 29:
            raise ValidationError(
                "Could not calculate the open-ended validation boundary"
            ) from exc
        anniversary = local_start.replace(year=target_year, day=28)

    if mode == RecurrenceMode.UTC_STEP:
        anniversary_utc = anniversary
    else:
        if timezone_name is None:
            raise ValidationError("TEACHER_LOCAL series requires a recurrence timezone")
        anniversary_utc = resolve_local_datetime(anniversary, timezone_name)
    return normalize_utc(anniversary_utc)


def expand_recurrence(
    series: RecurrenceSeries,
    range_start: datetime,
    range_end: datetime,
    *,
    max_occurrences: int = MAX_OCCURRENCES_PER_QUERY,
) -> list[Occurrence]:
    """Expand a series into occurrences overlapping a half-open UTC range."""
    start = _as_utc(series.start, "series start")
    end = _as_utc(series.end, "series end")
    cutoff = (
        _as_utc(series.interval_end, "series cutoff")
        if series.interval_end is not None
        else None
    )
    query_start = _as_utc(range_start, "query start")
    query_end = _as_utc(range_end, "query end")
    mode = _recurrence_mode(series.recurrence_mode)

    if series.source.kind != OccurrenceSourceKind.RECURRENT_EVENT:
        raise ValidationError("Recurrence source must be a recurrent event")
    if not isinstance(series.interval, int) or isinstance(series.interval, bool):
        raise ValidationError("Recurrence interval must be a positive integer")
    if series.interval <= 0:
        raise ValidationError("Recurrence interval must be positive")
    if start >= end:
        raise ValidationError("Series start time must be before end time")
    if cutoff is not None and cutoff <= start:
        raise ValidationError("Series cutoff must be after its first occurrence")
    if query_start >= query_end:
        raise ValidationError("Query start time must be before query end time")
    if not isinstance(max_occurrences, int) or isinstance(max_occurrences, bool):
        raise ValidationError("Occurrence limit must be a positive integer")
    if max_occurrences <= 0:
        raise ValidationError("Occurrence limit must be positive")
    if max_occurrences > MAX_OCCURRENCES_PER_QUERY:
        raise ValidationError(
            f"Occurrence limit cannot exceed {MAX_OCCURRENCES_PER_QUERY}"
        )

    duration = end - start
    try:
        step = timedelta(days=series.interval)
    except OverflowError as exc:
        raise ValidationError(
            "Recurrence interval is outside the supported range"
        ) from exc
    if mode == RecurrenceMode.UTC_STEP:
        if series.recurrence_timezone is not None:
            raise ValidationError("UTC_STEP series must not have a recurrence timezone")
        occurrences = _expand_utc_step(
            series,
            start,
            cutoff,
            query_start,
            query_end,
            duration,
            step,
            max_occurrences,
        )
    else:
        if not series.recurrence_timezone:
            raise ValidationError("TEACHER_LOCAL series requires a recurrence timezone")
        zone = get_timezone(series.recurrence_timezone)
        occurrences = _expand_teacher_local(
            series,
            start,
            cutoff,
            query_start,
            query_end,
            duration,
            step,
            zone,
            zone.key,
            max_occurrences,
        )

    return sort_occurrences(occurrences)


def sort_occurrences(occurrences: Iterable[Occurrence]) -> list[Occurrence]:
    return sorted(occurrences, key=_occurrence_sort_key)


def _expand_utc_step(
    series: RecurrenceSeries,
    anchor: datetime,
    cutoff: datetime | None,
    query_start: datetime,
    query_end: datetime,
    duration: timedelta,
    step: timedelta,
    max_occurrences: int,
) -> list[Occurrence]:
    search_start = _subtract(query_start, duration, "range lower bound")
    index = max(0, (search_start - anchor) // step)
    results: list[Occurrence] = []
    generated = 0

    for occurrence_index in count(index):
        step_delta = _scaled_step(step, occurrence_index, "UTC recurrence interval")
        occurrence_start = _add(anchor, step_delta, "UTC recurrence start")
        if (
            cutoff is not None and occurrence_start >= cutoff
        ) or occurrence_start >= query_end:
            break
        generated += 1
        _check_budget(generated, max_occurrences)
        occurrence_end = _add(occurrence_start, duration, "UTC recurrence end")
        if intervals_overlap(occurrence_start, occurrence_end, query_start, query_end):
            results.append(Occurrence(series.source, occurrence_start, occurrence_end))

    return results


def _expand_teacher_local(
    series: RecurrenceSeries,
    anchor: datetime,
    cutoff: datetime | None,
    query_start: datetime,
    query_end: datetime,
    duration: timedelta,
    step: timedelta,
    zone: ZoneInfo,
    timezone_name: str,
    max_occurrences: int,
) -> list[Occurrence]:
    anchor_local = _local_wall_time(anchor, zone)
    search_start = _subtract(query_start, duration, "range lower bound")
    search_local = _local_wall_time(search_start, zone)
    search_local = _subtract(
        search_local, _LOCAL_TIME_SEARCH_PADDING, "local recurrence lower bound"
    )
    index = max(0, (search_local - anchor_local) // step)

    local_query_end = _local_wall_time(query_end, zone)
    local_cutoff = (
        _local_wall_time(cutoff, zone) if cutoff is not None else local_query_end
    )
    local_stop = _add(
        min(local_query_end, local_cutoff),
        _LOCAL_TIME_SEARCH_PADDING,
        "local recurrence upper bound",
    )

    results: list[Occurrence] = []
    seen_starts: set[datetime] = set()
    generated = 0

    for occurrence_index in count(index):
        step_delta = _scaled_step(step, occurrence_index, "local recurrence interval")
        local_start = _add(
            anchor_local,
            step_delta,
            "local recurrence start",
        )
        if local_start > local_stop:
            break

        generated += 1
        _check_budget(generated, max_occurrences)
        occurrence_start = normalize_utc(
            resolve_local_datetime(local_start, timezone_name)
        )
        if occurrence_start in seen_starts:
            continue
        seen_starts.add(occurrence_start)

        if cutoff is not None and occurrence_start >= cutoff:
            continue
        occurrence_end = _add(occurrence_start, duration, "local recurrence end")
        if intervals_overlap(occurrence_start, occurrence_end, query_start, query_end):
            results.append(Occurrence(series.source, occurrence_start, occurrence_end))

    return results


def _as_utc(value: datetime, label: str) -> datetime:
    try:
        return normalize_utc(value)
    except (OverflowError, ValueError) as exc:
        raise ValidationError(f"Invalid {label}") from exc


def _recurrence_mode(value: RecurrenceMode | str) -> RecurrenceMode:
    try:
        return RecurrenceMode(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid recurrence mode: {value}") from exc


def _local_wall_time(value: datetime, zone: ZoneInfo) -> datetime:
    try:
        return value.astimezone(zone).replace(tzinfo=None)
    except (OverflowError, ValueError) as exc:
        raise ValidationError(
            "Could not convert a recurrence boundary to local time"
        ) from exc


def _add(value: datetime, delta: timedelta, label: str) -> datetime:
    try:
        return value + delta
    except OverflowError as exc:
        raise ValidationError(
            f"{label} is outside the supported datetime range"
        ) from exc


def _subtract(value: datetime, delta: timedelta, label: str) -> datetime:
    try:
        return value - delta
    except OverflowError as exc:
        raise ValidationError(
            f"{label} is outside the supported datetime range"
        ) from exc


def _scaled_step(step: timedelta, index: int, label: str) -> timedelta:
    try:
        return step * index
    except OverflowError as exc:
        raise ValidationError(f"{label} is outside the supported range") from exc


def _check_budget(generated: int, maximum: int) -> None:
    if generated > maximum:
        raise ValidationError(
            f"Occurrence expansion exceeds the limit of {maximum} generated occurrences"
        )


def _occurrence_sort_key(
    occurrence: Occurrence,
) -> tuple[datetime, datetime, str, int]:
    return (
        occurrence.start,
        occurrence.end,
        occurrence.source.kind.value,
        occurrence.source.id.int,
    )
