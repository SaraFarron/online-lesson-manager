from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.occurrences import (
    Occurrence,
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    expand_recurrence,
    sort_occurrences,
)
from backend.events.time_utils import from_storage_utc, normalize_utc, to_storage_utc
from backend.exceptions import ValidationError

MAX_RANGE_DURATION = timedelta(days=31)


async def load_raw_occurrences(
    session: AsyncSession,
    teacher_id: UUID,
    range_start: datetime,
    range_end: datetime,
) -> list[Occurrence]:
    """Load active occurrences for one teacher over a half-open UTC range."""
    try:
        start = normalize_utc(range_start)
        end = normalize_utc(range_end)
    except (OverflowError, ValueError) as exc:
        raise ValidationError("Invalid occurrence query range") from exc
    if start >= end:
        raise ValidationError("Range start time must be before range end time")
    if end - start > MAX_RANGE_DURATION:
        raise ValidationError("Occurrence query range cannot exceed 31 days")

    stored_start = to_storage_utc(start)
    stored_end = to_storage_utc(end)
    events = (
        await session.scalars(
            select(Event).where(
                Event.teacher_id == teacher_id,
                Event.start < stored_end,
                Event.end > stored_start,
            )
        )
    ).all()

    # Without a duration limit, even a series cutoff before range_start may have a
    # last full occurrence crossing the range. Load earlier anchors and let expansion
    # apply the exact cutoff and overlap rules.
    recurrent_events = (
        await session.scalars(
            select(RecurrentEvent).where(
                RecurrentEvent.teacher_id == teacher_id,
                RecurrentEvent.start < stored_end,
            )
        )
    ).all()

    cancellations = (
        await session.scalars(
            select(RecurrentCancels)
            .join(
                RecurrentEvent,
                RecurrentCancels.recurrent_event_id == RecurrentEvent.id,
            )
            .where(
                RecurrentEvent.teacher_id == teacher_id,
                RecurrentEvent.start < stored_end,
                RecurrentCancels.start < stored_end,
                RecurrentCancels.end > stored_start,
            )
        )
    ).all()
    cancellations_by_series = _index_cancellations(cancellations)

    occurrences = [_one_off_occurrence(event) for event in events]
    for recurrent_event in recurrent_events:
        source = OccurrenceSource(
            kind=OccurrenceSourceKind.RECURRENT_EVENT,
            id=recurrent_event.id,
            teacher_id=recurrent_event.teacher_id,
            user_id=recurrent_event.user_id,
            event_type=_event_type(recurrent_event.event_type),
        )
        series = RecurrenceSeries(
            source=source,
            start=recurrent_event.start,
            end=recurrent_event.end,
            interval=recurrent_event.interval,
            interval_end=recurrent_event.interval_end,
            recurrence_mode=recurrent_event.recurrence_mode,
            recurrence_timezone=recurrent_event.recurrence_timezone,
        )
        series_occurrences = expand_recurrence(series, start, end)
        occurrence_by_start = {
            occurrence.start: occurrence for occurrence in series_occurrences
        }
        canceled_starts: set[datetime] = set()
        for cancellation_start, cancellation in cancellations_by_series.get(
            recurrent_event.id, {}
        ).items():
            cancellation_end = from_storage_utc(cancellation.end)
            occurrence = occurrence_by_start.get(cancellation_start)
            if occurrence is not None:
                if cancellation_end != occurrence.end:
                    raise ValidationError(
                        "Stored cancellation does not match a full generated occurrence"
                    )
                canceled_starts.add(cancellation_start)
            elif cancellation_start < end and cancellation_end > start:
                raise ValidationError(
                    "Stored cancellation does not match a generated occurrence"
                )

        occurrences.extend(
            occurrence
            for occurrence in series_occurrences
            if occurrence.start not in canceled_starts
        )

    return sort_occurrences(occurrences)


def _one_off_occurrence(event: Event) -> Occurrence:
    start = from_storage_utc(event.start)
    end = from_storage_utc(event.end)
    if start >= end:
        raise ValidationError(f"Event {event.id} has an invalid time range")
    return Occurrence(
        source=OccurrenceSource(
            kind=OccurrenceSourceKind.EVENT,
            id=event.id,
            teacher_id=event.teacher_id,
            user_id=event.user_id,
            event_type=_event_type(event.event_type),
        ),
        start=start,
        end=end,
    )


def _event_type(value: str) -> EventType:
    try:
        return EventType(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid stored event type: {value}") from exc


def _index_cancellations(
    cancellations: Sequence[RecurrentCancels],
) -> dict[UUID, dict[datetime, RecurrentCancels]]:
    indexed: dict[UUID, dict[datetime, RecurrentCancels]] = {}
    for cancellation in cancellations:
        start = from_storage_utc(cancellation.start)
        end = from_storage_utc(cancellation.end)
        if start >= end:
            raise ValidationError(
                f"Cancellation {cancellation.id} has an invalid time range"
            )
        cancellations_by_start = indexed.setdefault(cancellation.recurrent_event_id, {})
        if start in cancellations_by_start:
            raise ValidationError(
                "Multiple stored cancellations identify the same occurrence"
            )
        cancellations_by_start[start] = cancellation
    return indexed
