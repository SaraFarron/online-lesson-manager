import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from backend.notifications.config import (
    NotificationConfig,
    get_notification_settings,
)

notification_service_key = APIKeyHeader(
    name="X-Notification-Service-Key", auto_error=False
)


async def authenticate_notification_service(
    credential: Annotated[str | None, Depends(notification_service_key)],
    config: Annotated[NotificationConfig, Depends(get_notification_settings)],
) -> None:
    if config.SERVICE_CREDENTIAL is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="notification_service_not_configured",
        )
    expected = config.SERVICE_CREDENTIAL.get_secret_value()
    if not credential or not secrets.compare_digest(
        credential.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_notification_service_credential",
        )
