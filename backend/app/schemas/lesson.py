from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LessonBase(BaseModel):
    """Base schema for Lesson with shared attributes."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    content: str | None = None
    duration_minutes: int | None = Field(None, ge=1)
    is_published: bool = False


class LessonCreate(LessonBase):
    """Schema for creating a new Lesson."""

    pass


class LessonUpdate(BaseModel):
    """Schema for updating an existing Lesson. All fields optional."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    content: str | None = None
    duration_minutes: int | None = Field(None, ge=1)
    is_published: bool | None = None


class LessonResponse(LessonBase):
    """Schema for Lesson response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
