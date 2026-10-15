import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from backend.constants import EventType
from backend.events.time_utils import normalize_utc

UtcTimestamp = Annotated[
    datetime,
    AfterValidator(normalize_utc),
    Field(
        description=(
            "An instant in time. Naive timestamps are interpreted as UTC; "
            "timestamps with an offset are converted to UTC."
        ),
        examples=[
            "2026-12-01T09:00:00Z",
            "2026-12-01T16:00:00+07:00",
            "2026-12-01T09:00:00",
        ],
    ),
]
PositiveInterval = Annotated[
    int, Field(gt=0, strict=True, description="Recurrence interval in whole days.")
]


def _validate_time_range(
    start: datetime, end: datetime, interval_end: datetime | None = None
) -> None:
    if start >= end:
        raise ValueError("Event start time must be before end time")
    if interval_end is not None and interval_end <= start:
        raise ValueError("Recurrence cutoff must be after the first occurrence start")


class EventCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: UtcTimestamp
    end: UtcTimestamp
    user_id: uuid.UUID
    event_type: EventType

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        _validate_time_range(self.start, self.end)
        return self


class EventPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start: UtcTimestamp
    end: UtcTimestamp
    user_id: uuid.UUID
    event_type: EventType

    @field_serializer("start", "end", when_used="json")
    def serialize_utc_timestamps(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class EventUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    start: UtcTimestamp
    end: UtcTimestamp

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        _validate_time_range(self.start, self.end)
        return self


EventWrite = EventCreate | EventUpdate


class RecurrentEventCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: UtcTimestamp
    end: UtcTimestamp
    user_id: uuid.UUID
    event_type: EventType
    interval: PositiveInterval
    interval_end: UtcTimestamp

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        _validate_time_range(self.start, self.end, self.interval_end)
        return self


class RecurrentEventPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start: UtcTimestamp
    end: UtcTimestamp
    user_id: uuid.UUID
    event_type: EventType
    interval: PositiveInterval
    interval_end: UtcTimestamp

    @field_serializer("start", "end", "interval_end", when_used="json")
    def serialize_utc_timestamps(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class RecurrentCancel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: UtcTimestamp
    end: UtcTimestamp

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        _validate_time_range(self.start, self.end)
        return self


class RecurrentEventUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    start: UtcTimestamp
    end: UtcTimestamp
    interval: PositiveInterval
    interval_end: UtcTimestamp
    cancels: list[RecurrentCancel] | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        _validate_time_range(self.start, self.end, self.interval_end)
        return self


RecurrentEventWrite = RecurrentEventCreate | RecurrentEventUpdate
