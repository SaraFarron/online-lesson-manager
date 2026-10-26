from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.homework.constants import MAX_ASSIGNMENT_ATTACHMENTS


class StagedAttachmentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    byte_size: int
    expires_at: datetime


class LessonSourceKind(StrEnum):
    EVENT = "EVENT"
    RECURRENT_EVENT = "RECURRENT_EVENT"


class HomeworkStatus(StrEnum):
    INCOMPLETE = "INCOMPLETE"
    COMPLETE = "COMPLETE"


class LessonOccurrenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LessonSourceKind
    source_id: UUID
    starts_at: datetime


class HomeworkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: UUID
    text: str | None = None
    attachment_ids: list[UUID] = Field(
        default_factory=list, max_length=MAX_ASSIGNMENT_ATTACHMENTS
    )
    lesson_occurrence: LessonOccurrenceReference | None = None

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Homework text must not be blank")
        return value

    @model_validator(mode="after")
    def require_text_or_attachments(self) -> HomeworkCreate:
        if self.text is None and not self.attachment_ids:
            raise ValueError("Homework requires text, attachments, or both")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Attachment IDs must be unique")
        return self


class HomeworkCreated(BaseModel):
    id: UUID
    status: HomeworkStatus
    material_count: int = Field(ge=0, le=MAX_ASSIGNMENT_ATTACHMENTS)
