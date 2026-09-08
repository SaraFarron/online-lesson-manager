import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.models import Event
from backend.events.schemas import EventCreate, EventUpdate
from backend.events.validators import EventValidator


async def create_event(
    session: AsyncSession, event_data: EventCreate, validator: EventValidator, teacher_id: uuid.UUID
) -> Event:
    await validator.validate_all(session, event_data, teacher_id=teacher_id, user_id=event_data.user_id)

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
    session: AsyncSession, event: Event, event_data: EventUpdate, validator: EventValidator, teacher_id: uuid.UUID
) -> Event:
    await validator.validate_all(session, event_data, user_id=event.user_id)

    event.start = event_data.start
    event.end = event_data.end
    event.teacher_id = teacher_id
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def delete_event(session: AsyncSession, event: Event) -> None:
    await session.delete(event)
    await session.commit()
