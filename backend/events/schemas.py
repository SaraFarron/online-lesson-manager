import uuid
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
)

from backend.constants import EventType


class EventCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    user_id: uuid.UUID
    event_type: EventType


class EventPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start: datetime
    end: datetime
    user_id: uuid.UUID
    event_type: EventType


class EventUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    start: datetime
    end: datetime


EventWrite = EventCreate | EventUpdate


class RecurrentEventCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    user_id: uuid.UUID
    event_type: EventType
    interval: int  # in days
    interval_end: datetime  # when the recurrence ends


class RecurrentEventPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start: datetime
    end: datetime
    user_id: uuid.UUID
    event_type: EventType
    interval: int  # in days
    interval_end: datetime  # when the recurrence ends


class RecurrentEventUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    start: datetime
    end: datetime
    interval: int  # in days
    interval_end: datetime  # when the recurrence ends


RecurrentEventWrite = RecurrentEventCreate | RecurrentEventUpdate
