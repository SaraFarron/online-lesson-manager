from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, User
from app.repositories.base import BaseRepository


class LessonRepository(BaseRepository[Event]):
    """Repository for Lesson-specific database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(Event, session)

    async def get_published(
        self, skip: int = 0, limit: int = 100
    ) -> list[Event]:
        """Get only published lessons."""
        query = (
            select(Event)
            .where(Event.is_published == True)  # noqa: E712
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def search_by_title(self, title: str) -> list[Event]:
        """Search lessons by title (case-insensitive)."""
        query = select(Event).where(Event.title.ilike(f"%{title}%"))
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_day(self, day: date, user: User) -> list[Event]:
        """Get lessons for a specific day."""
        day_start = datetime.combine(day, datetime.min.time())
        day_end = datetime.combine(day, datetime.max.time())
        if user.role == "teacher":
            query = select(Event).where(
                Event.title == Event.Types.LESSON,
                Event.start >= day_start,
                Event.start <= day_end,
                Event.teacher_id == user.id,
            )
        else:
            query = select(Event).where(
                Event.title == Event.Types.LESSON,
                Event.start >= day_start,
                Event.start <= day_end,
                Event.student_id == user.id,
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_weekday(self, day_of_week: int) -> list[Event]:
        """Get lessons for a specific day of the week (0=Monday, 6=Sunday)."""
        query = select(Event).where()
        result = await self.session.execute(query)
        return list(result.scalars().all())
