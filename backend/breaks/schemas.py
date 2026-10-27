from typing import Literal, Self

from pydantic import model_validator

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

    @model_validator(mode="after")
    def require_recurrence_cutoff(self) -> Self:
        if self.interval_end is None:
            raise ValueError("Recurring breaks require a recurrence cutoff")
        return self
