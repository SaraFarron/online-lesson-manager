from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from backend.constants import EventType
from backend.events.occurrences import (
    Occurrence,
    OccurrenceSourceKind,
    sort_occurrences,
)
from backend.exceptions import ValidationError

type _IntervalIndex = tuple[tuple[datetime, ...], tuple[datetime, ...]]


class VacationVisibilityReason(StrEnum):
    TEACHER_VACATION = "teacher_vacation"
    STUDENT_VACATION = "student_vacation"


@dataclass(frozen=True, slots=True)
class HiddenOccurrence:
    occurrence: Occurrence
    reasons: tuple[VacationVisibilityReason, ...]


@dataclass(frozen=True, slots=True)
class VisibilityResult:
    visible: tuple[Occurrence, ...]
    hidden: tuple[HiddenOccurrence, ...]


def filter_vacation_visibility(
    occurrences: Sequence[Occurrence], teacher_id: UUID
) -> VisibilityResult:
    """Hide whole occurrences overlapping applicable vacations without mutation."""
    ordered_occurrences = sort_occurrences(occurrences)
    teacher_vacations: list[Occurrence] = []
    student_vacations: dict[UUID, list[Occurrence]] = {}

    for occurrence in ordered_occurrences:
        if occurrence.source.teacher_id != teacher_id:
            raise ValidationError("Occurrence belongs to a different teacher")
        if occurrence.source.event_type != EventType.VACATION:
            continue
        if occurrence.source.user_id == teacher_id:
            teacher_vacations.append(occurrence)
        else:
            student_vacations.setdefault(occurrence.source.user_id, []).append(
                occurrence
            )

    teacher_vacation_index = _build_interval_index(teacher_vacations)
    student_vacation_indexes = {
        user_id: _build_interval_index(vacations)
        for user_id, vacations in student_vacations.items()
    }
    visible: list[Occurrence] = []
    hidden: list[HiddenOccurrence] = []
    for occurrence in ordered_occurrences:
        visibility_start = occurrence.start
        visibility_end = occurrence.end
        if occurrence.source.kind == OccurrenceSourceKind.AUTOMATIC_BREAK:
            if (
                occurrence.source.parent_kind
                not in {
                    OccurrenceSourceKind.EVENT,
                    OccurrenceSourceKind.RECURRENT_EVENT,
                }
                or occurrence.source.parent_start is None
                or occurrence.source.parent_start >= occurrence.start
            ):
                raise ValidationError("Automatic break has an invalid parent interval")
            visibility_start = occurrence.source.parent_start
            visibility_end = occurrence.start

        reasons: list[VacationVisibilityReason] = []
        if _has_overlap(teacher_vacation_index, visibility_start, visibility_end):
            reasons.append(VacationVisibilityReason.TEACHER_VACATION)
        student_vacation_index = student_vacation_indexes.get(occurrence.source.user_id)
        if student_vacation_index is not None and _has_overlap(
            student_vacation_index, visibility_start, visibility_end
        ):
            reasons.append(VacationVisibilityReason.STUDENT_VACATION)

        if reasons:
            hidden.append(HiddenOccurrence(occurrence, tuple(reasons)))
        else:
            visible.append(occurrence)

    return VisibilityResult(tuple(visible), tuple(hidden))


def _build_interval_index(vacations: Sequence[Occurrence]) -> _IntervalIndex:
    starts: list[datetime] = []
    latest_ends: list[datetime] = []
    latest_end: datetime | None = None
    for vacation in sorted(vacations, key=lambda item: item.start):
        starts.append(vacation.start)
        latest_end = (
            vacation.end if latest_end is None else max(latest_end, vacation.end)
        )
        latest_ends.append(latest_end)
    return tuple(starts), tuple(latest_ends)


def _has_overlap(
    index: _IntervalIndex, occurrence_start: datetime, occurrence_end: datetime
) -> bool:
    starts, latest_ends = index
    boundary = bisect_left(starts, occurrence_end)
    return boundary > 0 and latest_ends[boundary - 1] > occurrence_start
