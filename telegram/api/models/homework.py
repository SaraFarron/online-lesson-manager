from __future__ import annotations

import uuid
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

MAX_HOMEWORK_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_HOMEWORK_ATTACHMENTS = 5


class HomeworkStudent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    full_name: str | None


class HomeworkLessonOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["EVENT", "RECURRENT_EVENT"]
    source_id: uuid.UUID
    starts_at: AwareDatetime


class HomeworkMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    file_name: str
    byte_size: int = Field(ge=0)


class HomeworkAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    student: HomeworkStudent | None = None
    text: str | None
    status: Literal["INCOMPLETE", "COMPLETE"]
    completion_source: Literal["STUDENT_SUBMISSION", "TEACHER"] | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None
    lesson_occurrence: HomeworkLessonOccurrence | None
    material_count: int = Field(ge=0, le=MAX_HOMEWORK_ATTACHMENTS)


class HomeworkAssignmentDetail(HomeworkAssignment):
    materials: list[HomeworkMaterial]


class HomeworkAssignmentPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[HomeworkAssignment]
    total: int = Field(ge=0)


class HomeworkAssignmentCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    status: Literal["INCOMPLETE", "COMPLETE"]
    material_count: int = Field(ge=0, le=MAX_HOMEWORK_ATTACHMENTS)


class HomeworkSubmissionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    attachment_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_HOMEWORK_ATTACHMENTS
    )
    operation_key: uuid.UUID

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Submission text must not be blank")
        return value

    @model_validator(mode="after")
    def require_content(self) -> Self:
        if self.text is None and not self.attachment_ids:
            raise ValueError("A submission requires text or at least one attachment")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Submission attachment IDs must be unique")
        return self


class HomeworkAssignmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: uuid.UUID
    text: str | None = None
    attachment_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_HOMEWORK_ATTACHMENTS
    )
    lesson_occurrence: HomeworkLessonOccurrence | None = None

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Homework text must not be blank")
        return value

    @model_validator(mode="after")
    def require_content(self) -> Self:
        if self.text is None and not self.attachment_ids:
            raise ValueError("An assignment requires text or at least one attachment")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Assignment attachment IDs must be unique")
        return self


class HomeworkSubmissionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    version: int = Field(ge=1)
    text: str | None
    created_at: AwareDatetime
    is_current: bool
    materials: list[HomeworkMaterial]


class HomeworkSubmissionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    homework_id: uuid.UUID
    items: list[HomeworkSubmissionReview]
    total: int = Field(ge=0)


class HomeworkSubmissionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    assignment_status: Literal["INCOMPLETE", "COMPLETE"]
    completion_source: Literal["STUDENT_SUBMISSION", "TEACHER"]


class HomeworkCompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["INCOMPLETE", "COMPLETE"]
    completion_source: Literal["STUDENT_SUBMISSION", "TEACHER"]
    completed_at: AwareDatetime


class StagedHomeworkAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    file_name: str
    byte_size: int = Field(ge=0, le=MAX_HOMEWORK_ATTACHMENT_BYTES)
    expires_at: AwareDatetime
