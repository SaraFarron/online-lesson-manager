from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from backend.events.time_utils import normalize_utc
from backend.notifications.constants import (
    MAX_CLAIM_BATCH,
    AcknowledgementOutcome,
    NotificationState,
    NotificationType,
)


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=MAX_CLAIM_BATCH, ge=1, le=MAX_CLAIM_BATCH)


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
    occurrence_start_at: datetime | None
    scheduled_at: datetime
    lease_token: UUID
    lease_expires_at: datetime

    @field_serializer(
        "occurrence_start_at",
        "scheduled_at",
        "lease_expires_at",
        when_used="json",
    )
    def serialize_utc_datetimes(self, value: datetime | None) -> str | None:
        return normalize_utc(value).isoformat() if value is not None else None


class ClaimResponse(BaseModel):
    items: list[ClaimedNotification]


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_token: UUID
    outcome: AcknowledgementOutcome
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)

    @model_validator(mode="after")
    def validate_outcome_fields(self) -> AcknowledgeRequest:
        if self.outcome == AcknowledgementOutcome.SENT:
            if self.error_code is not None or self.retry_after_seconds is not None:
                raise ValueError("Sent acknowledgements cannot include failure details")
        elif self.error_code is None:
            raise ValueError("Failure acknowledgements require an error code")
        if self.retry_after_seconds is not None and self.outcome not in {
            AcknowledgementOutcome.RETRYABLE,
            AcknowledgementOutcome.UNKNOWN,
        }:
            raise ValueError("Retry-after is valid only for retryable outcomes")
        return self


class AcknowledgeResponse(BaseModel):
    id: UUID
    state: NotificationState
    last_outcome: AcknowledgementOutcome
    available_at: datetime

    @field_serializer("available_at", when_used="json")
    def serialize_available_at(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class NotificationHealth(BaseModel):
    pending: int = Field(ge=0)
    claimed: int = Field(ge=0)
    retry: int = Field(ge=0)
    sent: int = Field(ge=0)
    terminal: int = Field(ge=0)
    suppressed: int = Field(ge=0)
