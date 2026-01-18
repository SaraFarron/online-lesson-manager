from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentEvent, User
from app.repositories.base import BaseRepository


class EventRepository(BaseRepository[Event]):
    """Repository for Event-specific database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(Event, session)

    async def get_by_user(self, user: User) -> list[Event]:
        """Get all events for a specific user."""
        if user.role == User.Roles.TEACHER:
            query = select(Event).where(
                Event.teacher_id == user.id,
            )
        else:
            query = select(Event).where(
                Event.student_id == user.id,
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())


class RecurrentEventRepository(BaseRepository[RecurrentEvent]):
    """Repository for RecurrentEvent-specific database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(RecurrentEvent, session)
