from backend.constants import EventType
from backend.events.schemas import EventCreate, EventPublic, EventUpdate


class BreakCreate(EventCreate):
    event_type: EventType = EventType.BREAK


class BreakPublic(EventPublic):
    event_type: EventType = EventType.BREAK


class BreakUpdate(EventUpdate):
    pass
