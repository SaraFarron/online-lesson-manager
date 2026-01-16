from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LessonBase(BaseModel):
    """Base schema for Lesson with shared attributes."""

    title: str
    start: datetime
    end: datetime
    teacher_id: int
    student_id: int
    is_reschedule: bool = False


class LessonCreate(LessonBase):
    """Schema for creating a new Lesson."""


class LessonMove(BaseModel):
    """Schema for moving lesson to a new datetime."""
    id: int
    start: datetime


class LessonResponse(LessonBase):
    """Schema for Lesson response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
