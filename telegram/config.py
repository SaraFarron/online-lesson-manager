from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BOT_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    TOKEN: SecretStr
    BACKEND_URL: str = "http://127.0.0.1:8000"
    SERVICE_CREDENTIAL: SecretStr = Field(min_length=32)
    REQUEST_TIMEOUT: float = Field(default=10, ge=1, le=60)
    RECOVERY_SECONDS: float = Field(default=15, ge=1, le=300)
    FAILURE_THRESHOLD: int = Field(default=3, ge=1, le=10)

    @field_validator("BACKEND_URL")
    @classmethod
    def validate_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Backend URL must use HTTP or HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "Backend URL must not contain credentials, query or fragment"
            )
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "app",
        }:
            raise ValueError("Remote backend URLs must use HTTPS")
        return value.rstrip("/")
