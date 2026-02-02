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


class TelegramCacheResponse(BaseModel):
    """
    Planned cache structure in Telegram bot
    "user_id": {
        "free_slots": {
            "01.01.2000": [["10:00", "14:00"], ["15:00", "17:00"]]  // all on month forward
        },
        "recurrent_free_slots": {
            "0": [["10:00", "14:00"], ["15:00", "17:00"]],
            "1": []
        },
        "schedule": {
            "01.01.2000": [{"type": "lesson", "start": "13:00"}]  // all on week forward
        },
        "user_settings": {}
    }
    """
    free_slots: dict[str, list[list[str]]]
    recurrent_free_slots: dict[str, list[list[str]]]
    schedule: dict[str, list[dict[str, str]]]
    user_settings: dict[str, str]  # Placeholder for user settings

    model_config = ConfigDict(from_attributes=True)
