from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.models import Event
from backend.events.schemas import EventCreate, EventUpdate
from backend.events.validators import EventValidator


async def create_event(session: AsyncSession, event_data: EventCreate, validator: EventValidator) -> Event:
    await validator.validate_all(session, event_data)

    event = Event(
        start=event_data.start,
        end=event_data.end,
        user_id=event_data.user_id,
        event_type=event_data.event_type,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def update_event(session: AsyncSession, event: Event, event_data: EventUpdate, validator: EventValidator) -> Event:
    await validator.validate_all(session, event_data, user_id=event.user_id)

    event.start = event_data.start
    event.end = event_data.end

    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def delete_event(session: AsyncSession, event: Event) -> None:
    await session.delete(event)
    await session.commit()
