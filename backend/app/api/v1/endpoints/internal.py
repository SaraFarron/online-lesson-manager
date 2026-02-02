from fastapi import APIRouter, HTTPException, status

from app.api.deps import DatabaseSession, ServiceKey
from app.schemas import NotificationResponse, NotificationUpdate, TelegramCacheResponse
from app.services import BotCacheService, NotificationService

router = APIRouter()


@router.get("/notifications/pending", response_model=list[NotificationResponse])
async def get_pending_notifications(
    db: DatabaseSession,
    x_service_key: ServiceKey,
):
    """Get all pending notifications."""
    service = NotificationService(db)
    notifications = await service.get_all_pending_notifications()
    return [NotificationResponse.model_validate(n) for n in notifications]


@router.patch("/notifications/{notification_id}", response_model=NotificationResponse)
async def update_notification_status(
    db: DatabaseSession,
    x_service_key: ServiceKey,
    notification_id: int,
    notification: NotificationUpdate,
):
    """Update notification status."""
    service = NotificationService(db)
    updated_notification = await service.update_status(notification_id, notification)
    if updated_notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification with id {notification_id} does not exist."
        )
    return NotificationResponse.model_validate(updated_notification)


@router.get("/schedule/{teacher_id}", response_model=dict[int, TelegramCacheResponse])
async def get_user_schedule(
    db: DatabaseSession,
    x_service_key: ServiceKey,
    user_id: int,
):
    """Get schedule for a specific teacher."""
    service = BotCacheService(db)
    return await service.get_user_schedule(user_id)
