from backend.constants import EventType
from backend.events.schemas import (
    EventCreate,
    EventPublic,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventPublic,
    RecurrentEventUpdate,
)


class LessonCreate(EventCreate):
    event_type: EventType = EventType.LESSON


class LessonPublic(EventPublic):
    event_type: EventType = EventType.LESSON


class LessonUpdate(EventUpdate):
    pass


class RecurrentLessonCreate(RecurrentEventCreate):
    event_type: EventType = EventType.LESSON


class RecurrentLessonPublic(RecurrentEventPublic):
    event_type: EventType = EventType.LESSON


class RecurrentLessonUpdate(RecurrentEventUpdate):
    pass
