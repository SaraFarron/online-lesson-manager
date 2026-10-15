from typing import Literal

from backend.constants import EventType
from backend.events.schemas import EventCreate, EventPublic, EventUpdate


class VacationCreate(EventCreate):
    event_type: Literal[EventType.VACATION] = EventType.VACATION


class VacationPublic(EventPublic):
    event_type: Literal[EventType.VACATION] = EventType.VACATION


class VacationUpdate(EventUpdate):
    event_type: Literal[EventType.VACATION] | None = None
