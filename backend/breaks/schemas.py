from typing import Literal

from backend.constants import EventType
from backend.events.schemas import (
    EventCreate,
    EventPublic,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventPublic,
    RecurrentEventUpdate,
    UtcTimestamp,
)


class BreakCreate(EventCreate):
    end: UtcTimestamp
    event_type: Literal[EventType.BREAK] = EventType.BREAK


class BreakPublic(EventPublic):
    event_type: Literal[EventType.BREAK] = EventType.BREAK


class BreakUpdate(EventUpdate):
    event_type: Literal[EventType.BREAK] | None = None


class RecurrentBreakCreate(RecurrentEventCreate):
    end: UtcTimestamp
    event_type: Literal[EventType.BREAK] = EventType.BREAK


class RecurrentBreakPublic(RecurrentEventPublic):
    event_type: Literal[EventType.BREAK] = EventType.BREAK


class RecurrentBreakUpdate(RecurrentEventUpdate):
    event_type: Literal[EventType.BREAK] | None = None
