from __future__ import annotations

import uuid
from datetime import date
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)


class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["event", "recurrent_event", "automatic_break"]
    source_id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"]
    parent_kind: Literal["event", "recurrent_event"] | None = None
    parent_start: AwareDatetime | None = None
    start: AwareDatetime
    end: AwareDatetime
    status: Literal["past", "ongoing", "upcoming"]
    subject_label: str | None = None

    @model_validator(mode="after")
    def validate_interval_and_label(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Schedule item start must be before end")
        if self.source_kind == "automatic_break":
            if (
                self.event_type != "BREAK"
                or self.parent_kind is None
                or self.parent_start is None
                or self.parent_start >= self.start
            ):
                raise ValueError("Automatic breaks require a valid parent occurrence")
        elif self.parent_kind is not None or self.parent_start is not None:
            raise ValueError("Only automatic breaks can include a parent occurrence")
        if self.subject_label is not None and (
            self.event_type != "LESSON" or not self.subject_label.strip()
        ):
            raise ValueError("Only named lessons may have a subject label")
        return self


class ScheduleDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    items: list[ScheduleItem]


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Literal["day", "week"]
    timezone: str
    generated_at: AwareDatetime
    range_start: AwareDatetime
    range_end: AwareDatetime
    days: list[ScheduleDay]

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid schedule timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_period_shape(self) -> Self:
        expected_days = 1 if self.period == "day" else 7
        if len(self.days) != expected_days:
            raise ValueError(
                f"{self.period} response must contain {expected_days} day(s)"
            )
        if any(
            later.date.toordinal() != earlier.date.toordinal() + 1
            for earlier, later in zip(self.days, self.days[1:], strict=False)
        ):
            raise ValueError("Schedule dates must be consecutive and ascending")
        if self.period == "week" and self.days[0].date.weekday() != 0:
            raise ValueError("Week response must start on Monday")
        if self.range_start >= self.range_end:
            raise ValueError("Schedule range start must be before end")
        return self
