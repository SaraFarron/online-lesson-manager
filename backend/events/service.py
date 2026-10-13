import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.breaks.schemas import RecurrentBreakCreate
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.schemas import (
    EventCreate,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventUpdate,
)
from backend.events.validators import EventValidator


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
        start=event_data.start,
        end=event_data.end,
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
        user_id=event.user_id,
        exclude_event_id=event.id,
    )

    event.start = event_data.start
    event.end = event_data.end
    event.teacher_id = teacher_id
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def delete_event(session: AsyncSession, event: Event | RecurrentEvent) -> None:
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

    recurrent_event = RecurrentEvent(
        start=event_data.start,
        end=event_data.end,
        user_id=event_data.user_id,
        teacher_id=teacher_id,
        event_type=event_data.event_type,
        interval=event_data.interval,
        interval_end=event_data.interval_end,
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
        user_id=recurrent_event.user_id,
        exclude_event_id=recurrent_event.id,
    )

    recurrent_event.start = event_data.start
    recurrent_event.end = event_data.end
    recurrent_event.teacher_id = teacher_id

    # Overwrite the existing cancels with the new ones
    if event_data.cancels is not None:
        await session.execute(
            delete(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == recurrent_event.id
            )
        )
        for cancel in event_data.cancels:
            new_cancel = RecurrentCancels(
                recurrent_event_id=recurrent_event.id,
                start=cancel.start,
                end=cancel.end,
            )
            session.add(new_cancel)

    session.add(recurrent_event)
    await session.commit()
    await session.refresh(recurrent_event)
    return recurrent_event
