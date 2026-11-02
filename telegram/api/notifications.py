from uuid import UUID

import httpx
from pydantic import ValidationError

from telegram.api.models.notifications import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    ClaimResponse,
)

NOTIFICATION_SERVICE_ROOT = "/api/v1/notifications/service"


class NotificationProtocolError(RuntimeError):
    pass


class NotificationClient:
    def __init__(self, http: httpx.AsyncClient, credential: str) -> None:
        self.http = http
        self.credential = credential

    async def claim(self, *, limit: int = 20) -> ClaimResponse:
        response = await self.http.post(
            f"{NOTIFICATION_SERVICE_ROOT}/claim",
            headers={"X-Notification-Service-Key": self.credential},
            json={"limit": limit},
        )
        response.raise_for_status()
        try:
            return ClaimResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise NotificationProtocolError(
                "Notification claim response was invalid"
            ) from exc

    async def acknowledge(
        self, intent_id: UUID, request: AcknowledgeRequest
    ) -> AcknowledgeResponse:
        response = await self.http.post(
            f"{NOTIFICATION_SERVICE_ROOT}/{intent_id}/ack",
            headers={"X-Notification-Service-Key": self.credential},
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        try:
            return AcknowledgeResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise NotificationProtocolError(
                "Notification acknowledgement response was invalid"
            ) from exc
