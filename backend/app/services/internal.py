from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification
from app.repositories import NotificationRepository
from app.services.users import UserSettingsService


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = NotificationRepository(session)

    async def get_all_pending_notifications(self):
        return await self.repository.get_pending()

    async def get_all_pending_notifications_day(self, day: date):
        return await self.repository.get_pending_day(day)


async def create_notifications_task(session: AsyncSession):
    """Creates new notifications for todays morning notification."""
    users_settings_service = UserSettingsService(session)
    active_user_settings = await users_settings_service.get_all_active_user_settings()
    new_notifications: list[Notification] = []
    today = datetime.now(UTC).date()
    for user_settings in active_user_settings:
        schedule_time = datetime.combine(today, user_settings.morning_notification)
        if schedule_time < datetime.now(UTC):
            schedule_time += timedelta(days=1)
        new_notifications.append(Notification(
            telegram_user_id=user_settings.user.telegram_id,
            message="Сегодня уроки",
            scheduled_at=schedule_time,
            status=Notification.Statuses.PENDING,
        ))
    session.add_all(new_notifications)
    await session.commit()

    return len(new_notifications)

