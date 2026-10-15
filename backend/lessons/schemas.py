from typing import Literal

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
    event_type: Literal[EventType.LESSON] = EventType.LESSON


class LessonPublic(EventPublic):
    event_type: Literal[EventType.LESSON] = EventType.LESSON


class LessonUpdate(EventUpdate):
    event_type: Literal[EventType.LESSON] | None = None


class RecurrentLessonCreate(RecurrentEventCreate):
    event_type: Literal[EventType.LESSON] = EventType.LESSON


class RecurrentLessonPublic(RecurrentEventPublic):
    event_type: Literal[EventType.LESSON] = EventType.LESSON


class RecurrentLessonUpdate(RecurrentEventUpdate):
    event_type: Literal[EventType.LESSON] | None = None
