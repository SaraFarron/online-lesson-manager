from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import NotificationStatus


class NotificationResponse(BaseModel):
    id: int
    telegram_user_id: int
    message: str
    scheduled_at: datetime
    status: str
    attempts: int

    model_config = ConfigDict(from_attributes=True)


class NotificationUpdate(BaseModel):
    attempts: int
    status: NotificationStatus
