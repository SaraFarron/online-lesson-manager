from __future__ import annotations

from datetime import date, datetime
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
from backend.events.occurrences import OccurrenceSourceKind
from backend.events.time_utils import normalize_utc

UtcTimestamp = Annotated[datetime, AfterValidator(normalize_utc)]


class SchedulePeriod(StrEnum):
    DAY = "day"
    WEEK = "week"


class ScheduleEventStatus(StrEnum):
    PAST = "past"
    ONGOING = "ongoing"
    UPCOMING = "upcoming"


class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: OccurrenceSourceKind
    source_id: UUID
    event_type: EventType
    start: UtcTimestamp
    end: UtcTimestamp
    status: ScheduleEventStatus

    @model_validator(mode="after")
    def validate_interval(self) -> ScheduleItem:
        if self.start >= self.end:
            raise ValueError("Schedule item start must be before end")
        return self

    @field_serializer("start", "end", when_used="json")
    def serialize_utc_timestamps(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class TeacherLessonScheduleItem(ScheduleItem):
    subject_label: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_lesson_label(self) -> TeacherLessonScheduleItem:
        if self.event_type != EventType.LESSON:
            raise ValueError("Only teacher lesson items can have a subject label")
        return self


class ScheduleDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    date: date
    items: list[ScheduleItem | TeacherLessonScheduleItem]


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    period: SchedulePeriod
    timezone: str
    generated_at: UtcTimestamp
    range_start: UtcTimestamp
    range_end: UtcTimestamp
    days: list[ScheduleDay]

    @model_validator(mode="after")
    def validate_period_shape(self) -> ScheduleResponse:
        expected_days = 1 if self.period == SchedulePeriod.DAY else 7
        if len(self.days) != expected_days:
            raise ValueError(
                f"{self.period.value} schedule must contain {expected_days} day(s)"
            )
        if any(
            later.date.toordinal() != earlier.date.toordinal() + 1
            for earlier, later in zip(self.days, self.days[1:], strict=False)
        ):
            raise ValueError("Schedule dates must be consecutive and ascending")
        if self.period == SchedulePeriod.WEEK and self.days[0].date.weekday() != 0:
            raise ValueError("Week schedule must start on Monday")
        if self.range_start >= self.range_end:
            raise ValueError("Schedule range start must be before end")
        return self

    @field_serializer("generated_at", "range_start", "range_end", when_used="json")
    def serialize_utc_timestamps(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()
