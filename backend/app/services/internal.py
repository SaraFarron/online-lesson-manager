
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import NotificationRepository


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = NotificationRepository(session)

    async def get_all_pending_notifications(self):
        return await self.repository.get_pending()
