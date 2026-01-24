from fastapi import APIRouter

from app.api.deps import DatabaseSession, ServiceKey
from app.schemas import NotificationResponse
from app.services import NotificationService

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
