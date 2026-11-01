from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from backend.auth.dependencies import SessionDep
from backend.notifications import service
from backend.notifications.config import (
    NotificationConfig,
    get_notification_settings,
)
from backend.notifications.dependencies import authenticate_notification_service
from backend.notifications.schemas import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    ClaimRequest,
    ClaimResponse,
    NotificationHealth,
)

router = APIRouter(
    prefix="/notifications/service",
    tags=["notifications"],
    dependencies=[Depends(authenticate_notification_service)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid notification-service credential"
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Notification-service credential is not configured"
        },
    },
)


def get_notification_clock() -> datetime:
    return datetime.now(UTC)


NotificationClockDep = Annotated[datetime, Depends(get_notification_clock)]
NotificationConfigDep = Annotated[
    NotificationConfig, Depends(get_notification_settings)
]


@router.get(
    "/health",
    response_model=NotificationHealth,
    summary="Read non-sensitive notification queue counts",
)
async def notification_health(session: SessionDep) -> NotificationHealth:
    return await service.get_health_counts(session)


@router.post(
    "/claim",
    response_model=ClaimResponse,
    summary="Claim a bounded batch of due Telegram notification intents",
    description=(
        "Notification workers only. Claims use 60-second leases and expose routing "
        "addresses only to callers with the separate notification-service credential."
    ),
)
async def claim_notifications(
    session: SessionDep,
    request: ClaimRequest,
    now: NotificationClockDep,
    config: NotificationConfigDep,
) -> ClaimResponse:
    items = await service.claim_pending_intents(
        session,
        now=now,
        limit=min(request.limit, config.CLAIM_BATCH_LIMIT),
    )
    return ClaimResponse(items=items)


@router.post(
    "/{intent_id}/ack",
    response_model=AcknowledgeResponse,
    summary="Acknowledge one notification send attempt",
    responses={409: {"description": "Claim lease is stale or no longer owned"}},
)
async def acknowledge_notification(
    intent_id: UUID,
    session: SessionDep,
    request: AcknowledgeRequest,
    now: NotificationClockDep,
) -> AcknowledgeResponse:
    return await service.acknowledge_intent(session, intent_id, request, now=now)
