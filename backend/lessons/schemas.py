import uuid
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
)


class LessonCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    student_id: uuid.UUID
    teacher_id: uuid.UUID


class LessonPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start: datetime
    end: datetime
    student_id: uuid.UUID
    teacher_id: uuid.UUID

