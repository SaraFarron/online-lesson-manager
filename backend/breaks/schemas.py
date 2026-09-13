from backend.constants import EventType
from backend.events.schemas import (
    EventCreate,
    EventPublic,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventPublic,
    RecurrentEventUpdate,
)


class BreakCreate(EventCreate):
    event_type: EventType = EventType.BREAK


class BreakPublic(EventPublic):
    event_type: EventType = EventType.BREAK


class BreakUpdate(EventUpdate):
    pass


class RecurrentBreakCreate(RecurrentEventCreate):
    event_type: EventType = EventType.BREAK


class RecurrentBreakPublic(RecurrentEventPublic):
    event_type: EventType = EventType.BREAK


class RecurrentBreakUpdate(RecurrentEventUpdate):
    pass
