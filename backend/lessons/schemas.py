from backend.constants import EventType
from backend.events.schemas import EventCreate, EventPublic, EventUpdate


class LessonCreate(EventCreate):
    event_type: EventType = EventType.LESSON


class LessonPublic(EventPublic):
    event_type: EventType = EventType.LESSON


class LessonUpdate(EventUpdate):
    pass
