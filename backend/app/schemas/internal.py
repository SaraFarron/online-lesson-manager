from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    telegram_user_id: int
    message: str
    scheduled_at: datetime
    status: str
    attempts: int
