from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, Field, field_validator

from app.models import Event, RecurrentEvent, User


class EventResponse(BaseModel):
    """Response model for events endpoints."""

    id: int
    title: str
    start: str
    duration: int
    isRecurring: bool

    @classmethod
    def from_models(cls, event: Event | RecurrentEvent):
        return EventResponse(
            id=event.id,
            title=event.title,
            start=event.start.isoformat(),
            duration=round((event.end - event.start).total_seconds() / 60),
            isRecurring=isinstance(event, RecurrentEvent),
        )


class EventsTotalResponse(BaseModel):
    """Response model for total events count."""

    events: list[EventResponse]
    total: int


class BaseEvent(BaseModel):
    title: str
    start: datetime
    duration: int = Field(ge=5)
    isRecurring: bool = False

    @field_validator("start")
    @classmethod
    def validate_utc_only(cls, v: datetime) -> datetime:
        """Ensure datetime is timezone-aware and in UTC."""
        if v.tzinfo is None:
            raise ValueError('Datetime must include timezone information (e.g., "2026-02-10T09:00:00Z")')

        # Check if timezone is UTC
        if v.tzinfo != UTC and v.utcoffset() != timedelta(0):
            raise ValueError("Only UTC timezone is allowed. Please convert to UTC before sending.")

        # Normalize to UTC
        return v.astimezone(UTC)

    def to_dict(self, user: User) -> dict[str, datetime | str | int | None]:
        """Convert to dictionary with timezone-aware datetime."""
        end = self.start + timedelta(minutes=self.duration)
        teacher_id = user.teacher_id if user.role == User.Roles.STUDENT else user.id
        res = {
            "title": self.title,
            "start": self.start,
            "end": end,
            "teacher_id": teacher_id,
            "student_id": user.id,
        }
        if self.isRecurring:
            res["interval_days"] = 7
            res["interval_end"] = None
        return res


class EventCreate(BaseEvent):
    """Schema for creating a new Event."""


class EventUpdate(BaseEvent):
    """Schema for updating an existing Event."""


class EventMove(BaseModel):
    """Schema for moving recurrent event occurrence."""

    cancel_date: date
    new_start: datetime
