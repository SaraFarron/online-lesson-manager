from datetime import datetime, timedelta

from pydantic import BaseModel

from app.models import User


class EventResponse(BaseModel):
    """Response model for events endpoints."""

    id: int
    title: str
    date: str
    startTime: str
    duration: int
    isRecurring: bool


class EventsTotalResponse(BaseModel):
    """Response model for total events count."""

    events: list[EventResponse]
    total: int


class EventCreate(BaseModel):
    """Schema for creating a new Event."""

    title: str
    date: str
    startTime: str
    duration: int
    isRecurring: bool = False

    def to_dict(self, user: User) -> dict:
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
