from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_", env_file=".env", extra="ignore"
    )

    SERVICE_CREDENTIAL: SecretStr | None = Field(default=None, min_length=32)


telegram_settings = TelegramConfig()
