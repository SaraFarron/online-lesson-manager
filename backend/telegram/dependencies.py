import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from backend.telegram.config import TelegramConfig, telegram_settings

service_key = APIKeyHeader(name="X-Bot-Service-Key", auto_error=False)


def get_telegram_settings() -> TelegramConfig:
    return telegram_settings


async def authenticate_bot(
    credential: Annotated[str | None, Depends(service_key)],
    config: Annotated[TelegramConfig, Depends(get_telegram_settings)],
) -> None:
    if config.SERVICE_CREDENTIAL is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telegram_adapter_not_configured",
        )
    expected = config.SERVICE_CREDENTIAL.get_secret_value()
    if not credential or not secrets.compare_digest(
        credential.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_bot_service_credential",
        )
