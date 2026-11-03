import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer

from backend.events.time_utils import normalize_utc


class StudentActionHistoryItem(BaseModel):
    id: uuid.UUID
    action_type: str
    occurred_at: datetime
    resource_type: str | None
    resource_id: uuid.UUID | None
    resource_occurrence_at: datetime | None
    resource_available: bool | None = Field(
        description="False means the UI should show 'Item no longer available'."
    )

    @field_serializer(
        "occurred_at",
        "resource_occurrence_at",
        when_used="json-unless-none",
    )
    def serialize_datetimes(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()
