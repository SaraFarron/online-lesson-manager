from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentCancels, RecurrentEvent, User
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

    async def get_by_user(self, user: User) -> list[RecurrentEvent]:
        if user.role == User.Roles.TEACHER:
            query = select(RecurrentEvent).where(
                RecurrentEvent.teacher_id == user.id,
            )
        else:
            query = select(RecurrentEvent).where(
                RecurrentEvent.student_id == user.id
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())


class RecurrentCancelsRepository(BaseRepository[RecurrentCancels]):
    """Repository for RecurrentEvent cancellation-specific database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(RecurrentCancels, session)

    async def get_cancels_by_recurrent_events(self, recurrent_event_ids: list[int]) -> list[RecurrentCancels]:
        """Get all cancellations for specific recurrent events."""
        query = select(RecurrentCancels).where(
            RecurrentCancels.id.in_(recurrent_event_ids),
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
