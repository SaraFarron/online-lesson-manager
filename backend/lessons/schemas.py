import uuid
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
)

from backend.constants import EventType


class LessonCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    user_id: uuid.UUID
    event_type: EventType = EventType.LESSON


class LessonPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start: datetime
    end: datetime
    user_id: uuid.UUID
    event_type: EventType


class LessonUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
