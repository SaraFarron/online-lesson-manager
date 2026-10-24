from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.constants import EventType
from backend.events.schemas import (
    EventCreate,
    EventPublic,
    EventUpdate,
    RecurrentCancel,
    RecurrentEventCreate,
    RecurrentEventPublic,
    RecurrentEventUpdate,
    UtcTimestamp,
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


class RecurrentOccurrenceCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_start: UtcTimestamp
    occurrence_end: UtcTimestamp

    @model_validator(mode="after")
    def validate_occurrence(self) -> Self:
        if self.occurrence_start >= self.occurrence_end:
            raise ValueError("Occurrence start must be before end")
        return self


class RecurrentOccurrenceMoveRequest(RecurrentOccurrenceCancelRequest):
    replacement_start: UtcTimestamp
    replacement_end: UtcTimestamp

    @model_validator(mode="after")
    def validate_replacement(self) -> Self:
        if self.replacement_start >= self.replacement_end:
            raise ValueError("Replacement start must be before end")
        return self


class RecurrentOccurrenceActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cancelled_occurrence: RecurrentCancel
    revision: str = Field(min_length=64, max_length=64)
    replacement: LessonPublic | None = None
