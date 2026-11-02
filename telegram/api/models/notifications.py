from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

NotificationType = Literal[
    "DAILY_DIGEST",
    "LESSON_REMINDER",
    "HOMEWORK_REMINDER",
    "ASSIGNMENT_CREATED",
    "SUBMISSION_RECEIVED",
]
AcknowledgementOutcome = Literal["sent", "retryable", "terminal", "unknown"]


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
