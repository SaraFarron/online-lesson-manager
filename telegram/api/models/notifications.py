from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

NotificationType = Literal[
    "DAILY_DIGEST",
    "LESSON_REMINDER",
    "HOMEWORK_REMINDER",
    "ASSIGNMENT_CREATED",
    "SUBMISSION_RECEIVED",
    "TEACHER_BROADCAST",
]
AcknowledgementOutcome = Literal["sent", "retryable", "terminal", "unknown"]
BroadcastDeliveryStatus = Literal[
    "pending", "retrying", "sent", "suppressed", "failed", "unknown"
]


class ClaimedNotification(BaseModel):
    id: UUID
    recipient_id: UUID
    chat_id: int
    notification_type: NotificationType
    content: list[str]
    source_id: UUID | None
    source_version: int | None
    source_date: date | None
    occurrence_source_type: Literal["event", "recurrent_event"] | None
    occurrence_source_id: UUID | None
    occurrence_start_at: AwareDatetime | None
    scheduled_at: AwareDatetime
    lease_token: UUID
    lease_expires_at: AwareDatetime


class ClaimResponse(BaseModel):
    items: list[ClaimedNotification]


class AcknowledgeRequest(BaseModel):
    lease_token: UUID
    outcome: AcknowledgementOutcome
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)


class AcknowledgeResponse(BaseModel):
    id: UUID
    state: Literal["PENDING", "CLAIMED", "RETRY", "SENT", "TERMINAL", "SUPPRESSED"]
    last_outcome: Literal["SENT", "RETRYABLE", "TERMINAL", "UNKNOWN"]
    available_at: AwareDatetime


class TeacherBroadcastPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def reject_blank_text(self) -> Self:
        if not self.text.strip():
            raise ValueError("Broadcast text cannot be blank")
        return self


class TeacherBroadcastPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str = Field(min_length=1, max_length=2048)
    recipient_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    unsendable_count: int = Field(ge=0)


class TeacherBroadcastPublishRequest(TeacherBroadcastPreviewRequest):
    preview_token: str = Field(min_length=1, max_length=2048)


class TeacherBroadcastPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    operation_key: UUID
    status: Literal["queued"]
    recipient_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    unsendable_count: int = Field(ge=0)


class TeacherBroadcastResultCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0)
    retrying: int = Field(ge=0)
    sent: int = Field(ge=0)
    suppressed: int = Field(ge=0)
    failed: int = Field(ge=0)
    unknown: int = Field(ge=0)


class TeacherBroadcastRecipientResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: UUID
    student_name: str | None
    status: BroadcastDeliveryStatus


class TeacherBroadcastResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_key: UUID
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    counts: TeacherBroadcastResultCounts
    recipients: list[TeacherBroadcastRecipientResult]
