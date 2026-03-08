from datetime import date, datetime, time

from pydantic import BaseModel


class EventCreate(BaseModel):
    title: str
    day: date
    start: time
    duration: int = 60  # default duration in minutes
    is_recurrent: bool = False


class Vacation(BaseModel):
    id: int
    start: datetime
    end: datetime
