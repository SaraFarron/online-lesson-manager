from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.notifications.constants import MAX_CLAIM_BATCH


class NotificationConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOTIFICATIONS_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    SERVICE_CREDENTIAL: SecretStr | None = Field(default=None, min_length=32)
    PRODUCER_POLL_SECONDS: int = Field(default=30, ge=1, le=300)
    CLAIM_BATCH_LIMIT: int = Field(default=MAX_CLAIM_BATCH, ge=1, le=MAX_CLAIM_BATCH)


notification_settings = NotificationConfig()


def get_notification_settings() -> NotificationConfig:
    return notification_settings
