from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.models import Event, RecurrentEvent, User


class EventResponse(BaseModel):
    """Response model for events endpoints."""

    id: int
    title: str
    date: str
    startTime: str
    duration: int
    isRecurring: bool

    @classmethod
    def from_models(cls, event: Event | RecurrentEvent):
        return EventResponse(
            id=event.id,
            title=event.title,
            date=event.start.date().isoformat(),
            startTime=event.start.time().isoformat(),
            duration=round((event.end - event.start).total_seconds() / 60),
            isRecurring=isinstance(event, RecurrentEvent),
        )


class EventsTotalResponse(BaseModel):
    """Response model for total events count."""

    events: list[EventResponse]
    total: int


class EventCreate(BaseModel):
    """Schema for creating a new Event."""

    title: str
    date: str
    startTime: str
    duration: int = Field(ge=5)
    isRecurring: bool = False

    def to_dict(self, user: User) -> dict[str, datetime | str | int | None]:
        """Convert EventCreate to dictionary."""
        start = datetime.strptime(self.date + " " + self.startTime, "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=self.duration)
        teacher_id = user.teacher_id if user.role == User.Roles.STUDENT else user.id
        res = {
            "title": self.title,
            "start": start,
            "end": end,
            "teacher_id": teacher_id,
            "student_id": user.id,
        }
        if self.isRecurring:
            res["interval_days"] = 7
            res["interval_end"] = None
        return res
