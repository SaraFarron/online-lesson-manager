from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.events.time_utils import normalize_utc
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


class HomeworkCompletionSource(StrEnum):
    STUDENT_SUBMISSION = "STUDENT_SUBMISSION"
    TEACHER = "TEACHER"


class LessonOccurrenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LessonSourceKind
    source_id: UUID
    starts_at: datetime

    @field_serializer("starts_at", when_used="json")
    def serialize_starts_at(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


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


class HomeworkSubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    attachment_ids: list[UUID] = Field(
        default_factory=list, max_length=MAX_ASSIGNMENT_ATTACHMENTS
    )
    operation_key: UUID

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Submission text must not be blank")
        return value

    @model_validator(mode="after")
    def require_text_or_attachments(self) -> HomeworkSubmissionCreate:
        if self.text is None and not self.attachment_ids:
            raise ValueError("Submission requires text, attachments, or both")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Attachment IDs must be unique")
        return self


class HomeworkSubmissionCreated(BaseModel):
    version: int = Field(ge=1)
    assignment_status: HomeworkStatus
    completion_source: HomeworkCompletionSource


class HomeworkStudentSummary(BaseModel):
    id: UUID
    full_name: str | None


class HomeworkMaterialPublic(BaseModel):
    id: UUID
    file_name: str
    byte_size: int = Field(ge=0)


class HomeworkSubmissionReviewItem(BaseModel):
    id: UUID
    version: int = Field(ge=1)
    text: str | None
    created_at: datetime
    is_current: bool
    materials: list[HomeworkMaterialPublic]

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class HomeworkSubmissionPage(BaseModel):
    homework_id: UUID
    items: list[HomeworkSubmissionReviewItem]
    total: int = Field(ge=0)


class HomeworkCompletionPublic(BaseModel):
    status: HomeworkStatus
    completion_source: HomeworkCompletionSource
    completed_at: datetime

    @field_serializer("completed_at", when_used="json")
    def serialize_completed_at(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class HomeworkReadItem(BaseModel):
    id: UUID
    student: HomeworkStudentSummary | None = None
    text: str | None
    status: HomeworkStatus
    completion_source: HomeworkCompletionSource | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    lesson_occurrence: LessonOccurrenceReference | None
    material_count: int = Field(ge=0, le=MAX_ASSIGNMENT_ATTACHMENTS)

    @field_serializer("created_at", "updated_at", "completed_at", when_used="json")
    def serialize_timestamps(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return normalize_utc(value).isoformat()


class HomeworkReadDetail(HomeworkReadItem):
    materials: list[HomeworkMaterialPublic]


class HomeworkPage(BaseModel):
    items: list[HomeworkReadItem]
    total: int = Field(ge=0)
