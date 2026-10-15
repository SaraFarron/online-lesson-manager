import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.service import get_user_by_id
from backend.breaks.schemas import RecurrentBreakCreate
from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.occurrences import (
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    expand_recurrence,
)
from backend.events.schemas import (
    EventCreate,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventUpdate,
)
from backend.events.time_utils import from_storage_utc, to_storage_utc
from backend.events.validators import EventValidator
from backend.exceptions import ValidationError


async def create_event(
    session: AsyncSession,
    event_data: EventCreate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
) -> Event:
    await validator.validate_all(
        session, event_data, teacher_id=teacher_id, user_id=event_data.user_id
    )

    event = Event(
        start=to_storage_utc(event_data.start),
        end=to_storage_utc(event_data.end),
        user_id=event_data.user_id,
        teacher_id=teacher_id,
        event_type=event_data.event_type,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def update_event(
    session: AsyncSession,
    event: Event,
    event_data: EventUpdate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
) -> Event:
    await validator.validate_all(
        session,
        event_data,
        teacher_id=teacher_id,
        user_id=event_data.user_id,
        exclude_event_id=event.id,
    )

    event.start = to_storage_utc(event_data.start)
    event.end = to_storage_utc(event_data.end)
    event.user_id = event_data.user_id
    event.teacher_id = teacher_id
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def delete_event(session: AsyncSession, event: Event | RecurrentEvent) -> None:
    if isinstance(event, RecurrentEvent):
        await session.execute(
            delete(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == event.id
            )
        )
    await session.delete(event)
    await session.commit()


async def create_recurrent_event(
    session: AsyncSession,
    event_data: RecurrentEventCreate | RecurrentBreakCreate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
) -> RecurrentEvent:
    await validator.validate_all(
        session, event_data, teacher_id=teacher_id, user_id=event_data.user_id
    )

    teacher = await get_user_by_id(session, teacher_id)
    if teacher is None:
        raise ValidationError(f"Teacher with ID {teacher_id} does not exist")

    recurrent_event = RecurrentEvent(
        start=to_storage_utc(event_data.start),
        end=to_storage_utc(event_data.end),
        user_id=event_data.user_id,
        teacher_id=teacher_id,
        event_type=event_data.event_type,
        interval=event_data.interval,
        interval_end=to_storage_utc(event_data.interval_end),
        recurrence_mode=RecurrenceMode.TEACHER_LOCAL.value,
        recurrence_timezone=teacher.timezone,
    )
    session.add(recurrent_event)
    await session.commit()
    await session.refresh(recurrent_event)
    return recurrent_event


async def update_recurrent_event(
    session: AsyncSession,
    recurrent_event: RecurrentEvent,
    event_data: RecurrentEventUpdate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
) -> RecurrentEvent:
    await validator.validate_all(
        session,
        event_data,
        teacher_id=teacher_id,
        user_id=event_data.user_id,
        exclude_event_id=recurrent_event.id,
    )

    recurrence_changed = (
        recurrent_event.start != to_storage_utc(event_data.start)
        or recurrent_event.end != to_storage_utc(event_data.end)
        or recurrent_event.interval != event_data.interval
        or recurrent_event.interval_end != to_storage_utc(event_data.interval_end)
    )
    replacement_cancellations = (
        _validate_cancellations(recurrent_event, event_data)
        if event_data.cancels is not None
        else None
    )

    recurrent_event.start = to_storage_utc(event_data.start)
    recurrent_event.end = to_storage_utc(event_data.end)
    recurrent_event.user_id = event_data.user_id
    recurrent_event.interval = event_data.interval
    recurrent_event.interval_end = to_storage_utc(event_data.interval_end)
    recurrent_event.teacher_id = teacher_id

    if recurrence_changed or replacement_cancellations is not None:
        await session.execute(
            delete(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == recurrent_event.id
            )
        )
        if replacement_cancellations is not None:
            session.add_all(
                RecurrentCancels(
                    recurrent_event_id=recurrent_event.id,
                    start=cancel_start,
                    end=cancel_end,
                )
                for cancel_start, cancel_end in replacement_cancellations
            )

    session.add(recurrent_event)
    await session.commit()
    await session.refresh(recurrent_event)
    return recurrent_event


def _validate_cancellations(
    recurrent_event: RecurrentEvent,
    event_data: RecurrentEventUpdate,
) -> list[tuple[datetime, datetime]]:
    try:
        event_type = EventType(recurrent_event.event_type)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid stored event type: {recurrent_event.event_type}"
        ) from exc

    source = OccurrenceSource(
        kind=OccurrenceSourceKind.RECURRENT_EVENT,
        id=recurrent_event.id,
        teacher_id=recurrent_event.teacher_id,
        user_id=event_data.user_id,
        event_type=event_type,
    )
    series = RecurrenceSeries(
        source=source,
        start=event_data.start,
        end=event_data.end,
        interval=event_data.interval,
        interval_end=event_data.interval_end,
        recurrence_mode=recurrent_event.recurrence_mode,
        recurrence_timezone=recurrent_event.recurrence_timezone,
    )
    duration = to_storage_utc(event_data.end) - to_storage_utc(event_data.start)
    validated: list[tuple[datetime, datetime]] = []
    seen_starts: set[datetime] = set()

    for cancel in event_data.cancels or []:
        cancel_start = to_storage_utc(cancel.start)
        cancel_end = to_storage_utc(cancel.end)
        if cancel_start in seen_starts:
            raise ValidationError("Duplicate cancellation for the same occurrence")
        seen_starts.add(cancel_start)
        if cancel_end - cancel_start != duration:
            raise ValidationError(
                "Cancellation must match the full generated occurrence interval"
            )
        validated.append((cancel_start, cancel_end))

    if not validated:
        return validated

    try:
        probe_start = from_storage_utc(min(start for start, _ in validated))
        probe_end = from_storage_utc(max(start for start, _ in validated)) + timedelta(
            microseconds=1
        )
    except OverflowError as exc:
        raise ValidationError("Cancellation is outside the supported range") from exc
    generated = {
        occurrence.start: occurrence.end
        for occurrence in expand_recurrence(series, probe_start, probe_end)
    }
    if any(
        generated.get(from_storage_utc(start)) != from_storage_utc(end)
        for start, end in validated
    ):
        raise ValidationError("Cancellation does not match a generated occurrence")

    return validated
