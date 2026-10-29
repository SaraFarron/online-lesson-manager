from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

ManagementResource = Literal[
    "lessons", "recurrent-lessons", "breaks", "recurrent-breaks", "vacations"
]


class EventManagementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"]
    start: AwareDatetime
    end: AwareDatetime
    user_id: uuid.UUID
    subject_label: str = Field(min_length=1)
    is_recurrent: bool
    cancelled_occurrences: int = Field(ge=0)
    visibility_status: (
        Literal["visible", "hidden_by_vacation", "not_applicable"] | None
    ) = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Managed event start must be before end")
        return self


class EventManagementPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EventManagementItem]
    total: int = Field(ge=0)


class RecurrentCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Cancellation start must be before end")
        return self


class ManagedOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime
    status: Literal["visible", "hidden_by_vacation", "cancelled"]

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Occurrence start must be before end")
        return self


class EventManagementDetail(EventManagementItem):
    post_break_minutes: int = Field(ge=0, strict=True)
    interval: int | None = Field(default=None, gt=0, strict=True)
    interval_end: AwareDatetime | None = None
    recurrence_mode: Literal["UTC_STEP", "TEACHER_LOCAL"] | None = None
    recurrence_timezone: str | None = None
    cancels: list[RecurrentCancel] = Field(default_factory=list)
    revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    inspected_occurrence: ManagedOccurrence | None = None

    @model_validator(mode="after")
    def validate_recurrence_shape(self) -> Self:
        recurrence_fields = (self.interval, self.recurrence_mode)
        if self.is_recurrent and any(value is None for value in recurrence_fields):
            raise ValueError("Recurring event details require recurrence metadata")
        if (
            self.is_recurrent
            and self.interval_end is None
            and (self.event_type != "LESSON" or self.interval != 7)
        ):
            raise ValueError("Only weekly lessons can have an open-ended recurrence")
        if not self.is_recurrent and (
            any(value is not None for value in (*recurrence_fields, self.interval_end))
            or self.recurrence_timezone is not None
            or self.cancels
        ):
            raise ValueError("One-off event details cannot contain recurrence metadata")
        return self


class EventWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    start: AwareDatetime
    end: AwareDatetime
    user_id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"]

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Event start must be before end")
        return self


class RecurrentEventWriteResult(EventWriteResult):
    interval: int = Field(gt=0, strict=True)
    interval_end: AwareDatetime | None

    @model_validator(mode="after")
    def validate_open_ended_shape(self) -> Self:
        if self.interval_end is None and (
            self.event_type != "LESSON" or self.interval != 7
        ):
            raise ValueError("Only weekly lessons can have an open-ended recurrence")
        return self


class EventCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime | None = None
    user_id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"]


class EventUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime
    user_id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"] | None = None


class RecurrentEventCreatePayload(EventCreatePayload):
    interval: int = Field(gt=0, strict=True)
    interval_end: AwareDatetime | None

    @model_validator(mode="after")
    def validate_open_ended_shape(self) -> Self:
        if self.interval_end is None and (
            self.event_type != "LESSON" or self.interval != 7
        ):
            raise ValueError("Only weekly lessons can have an open-ended recurrence")
        return self


class RecurrentEventUpdatePayload(EventUpdatePayload):
    event_type: Literal["LESSON", "BREAK", "VACATION"]
    interval: int = Field(gt=0, strict=True)
    interval_end: AwareDatetime | None
    cancels: list[RecurrentCancel] | None = None

    @model_validator(mode="after")
    def validate_open_ended_shape(self) -> Self:
        if self.interval_end is None and (
            self.event_type != "LESSON" or self.interval != 7
        ):
            raise ValueError("Only weekly lessons can have an open-ended recurrence")
        return self


class OccurrenceActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancelled_occurrence: RecurrentCancel
    revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    replacement: EventWriteResult | None = None


class RecurrentOccurrenceCancelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_start: AwareDatetime
    occurrence_end: AwareDatetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.occurrence_start >= self.occurrence_end:
            raise ValueError("Occurrence start must be before end")
        return self


class RecurrentOccurrenceMovePayload(RecurrentOccurrenceCancelPayload):
    replacement_start: AwareDatetime
    replacement_end: AwareDatetime

    @model_validator(mode="after")
    def validate_replacement(self) -> Self:
        if self.replacement_start >= self.replacement_end:
            raise ValueError("Replacement start must be before end")
        return self


class LocalIntervalResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def require_naive_local_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is not None:
            raise ValueError("Local datetimes must not include a timezone offset")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Local interval start must be before end")
        return self


class ResolvedLocalInstant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local: AwareDatetime
    utc: AwareDatetime
    utc_offset: str = Field(pattern=r"^[+-][0-9]{2}:[0-9]{2}$")

    @model_validator(mode="after")
    def validate_utc_offset(self) -> Self:
        if self.utc.utcoffset() != timedelta(0):
            raise ValueError("Resolved instant must be UTC")
        local_offset = self.local.strftime("%z")
        if self.utc_offset != f"{local_offset[:3]}:{local_offset[3:]}":
            raise ValueError("Local instant offset does not match its timezone offset")
        return self


class LocalIntervalResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str
    start: ResolvedLocalInstant
    end: ResolvedLocalInstant

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid resolution timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start.utc >= self.end.utc:
            raise ValueError("Resolved interval start must be before end")
        return self


class AutomaticBreakPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime
    duration_minutes: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Automatic break start must be before end")
        return self


class DisplacedOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["event", "recurrent_event"]
    source_id: uuid.UUID
    user_id: uuid.UUID
    event_type: Literal["LESSON"]
    start: AwareDatetime
    end: AwareDatetime
    automatic_break: AutomaticBreakPreview | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Affected occurrence start must be before end")
        return self


class BreakDisplacementPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["break_displacement_confirmation_required"]
    message: Literal["Confirm cancellation of overlapping lesson occurrences"]
    confirmation_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    affected_occurrences: list[DisplacedOccurrence] = Field(min_length=1)
