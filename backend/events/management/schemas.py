from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.schemas import (
    RecurrentCancel,
    UtcTimestamp,
    _validate_open_ended_recurrence,
)
from backend.events.time_utils import normalize_utc


class VisibilityStatus(StrEnum):
    VISIBLE = "visible"
    HIDDEN_BY_VACATION = "hidden_by_vacation"
    NOT_APPLICABLE = "not_applicable"


class ManagedOccurrenceStatus(StrEnum):
    VISIBLE = "visible"
    HIDDEN_BY_VACATION = "hidden_by_vacation"
    CANCELLED = "cancelled"


class ManagedOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: UtcTimestamp
    end: UtcTimestamp
    status: ManagedOccurrenceStatus

    @field_serializer("start", "end", when_used="json")
    def serialize_utc_timestamps(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class EventManagementItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    event_type: EventType
    start: UtcTimestamp
    end: UtcTimestamp
    user_id: UUID
    subject_label: str = Field(min_length=1)
    is_recurrent: bool
    cancelled_occurrences: int = Field(ge=0)
    visibility_status: VisibilityStatus | None = None

    @field_serializer("start", "end", when_used="json")
    def serialize_utc_timestamps(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class EventManagementPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[EventManagementItem]
    total: int = Field(ge=0)


class EventManagementDetail(EventManagementItem):
    post_break_minutes: int = Field(ge=0)
    interval: int | None = Field(default=None, gt=0)
    interval_end: UtcTimestamp | None = None
    recurrence_mode: RecurrenceMode | None = None
    recurrence_timezone: str | None = None
    cancels: list[RecurrentCancel] = Field(default_factory=list)
    revision: str = Field(min_length=64, max_length=64)
    inspected_occurrence: ManagedOccurrence | None = None

    @model_validator(mode="after")
    def validate_recurrence_shape(self) -> EventManagementDetail:
        recurrence_fields = (self.interval, self.recurrence_mode)
        if self.is_recurrent and any(value is None for value in recurrence_fields):
            raise ValueError("Recurring event details require recurrence metadata")
        if self.is_recurrent:
            _validate_open_ended_recurrence(
                self.event_type, self.interval or 0, self.interval_end
            )
        if not self.is_recurrent and (
            any(value is not None for value in (*recurrence_fields, self.interval_end))
            or self.recurrence_timezone is not None
            or self.cancels
        ):
            raise ValueError("One-off event details cannot contain recurrence metadata")
        return self

    @field_serializer("interval_end", when_used="json")
    def serialize_interval_end(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return normalize_utc(value).isoformat()


def _require_naive_local_datetime(value: datetime) -> datetime:
    if value.utcoffset() is not None:
        raise ValueError("Local datetimes must not include a timezone offset")
    return value


NaiveLocalDateTime = Annotated[datetime, AfterValidator(_require_naive_local_datetime)]


class LocalIntervalResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: NaiveLocalDateTime
    end: NaiveLocalDateTime

    @model_validator(mode="after")
    def validate_local_interval(self) -> LocalIntervalResolutionRequest:
        if self.start >= self.end:
            raise ValueError("Local interval start must be before end")
        return self


class ResolvedLocalInstant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local: datetime
    utc: UtcTimestamp
    utc_offset: str

    @field_serializer("local", when_used="json")
    def serialize_local_datetime(self, value: datetime) -> str:
        return value.isoformat()

    @field_serializer("utc", when_used="json")
    def serialize_utc_timestamp(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class LocalIntervalResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timezone: str
    start: ResolvedLocalInstant
    end: ResolvedLocalInstant
