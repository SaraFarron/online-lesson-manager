from backend.constants import EventType
from backend.events.schemas import EventCreate, EventPublic, EventUpdate


class VacationCreate(EventCreate):
    event_type: EventType = EventType.VACATION


class VacationPublic(EventPublic):
    event_type: EventType = EventType.VACATION


class VacationUpdate(EventUpdate):
    pass
