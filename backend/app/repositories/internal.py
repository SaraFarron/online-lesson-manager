from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification
from app.repositories import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    def __init__(self, session: AsyncSession):
        super().__init__(Notification, session)

    async def get_pending(self):
        query = select(Notification).where(
            Notification.status == Notification.Statuses.PENDING,
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_pending_day(self, day: date):
        query = select(Notification).where(
            Notification.status == Notification.Statuses.PENDING,
            Notification.scheduled_at == day
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
