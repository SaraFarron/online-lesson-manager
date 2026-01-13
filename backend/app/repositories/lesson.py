from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Lesson
from app.repositories.base import BaseRepository


class LessonRepository(BaseRepository[Lesson]):
    """Repository for Lesson-specific database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(Lesson, session)

    async def get_published(
        self, skip: int = 0, limit: int = 100
    ) -> list[Lesson]:
        """Get only published lessons."""
        query = (
            select(Lesson)
            .where(Lesson.is_published == True)  # noqa: E712
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def search_by_title(self, title: str) -> list[Lesson]:
        """Search lessons by title (case-insensitive)."""
        query = select(Lesson).where(Lesson.title.ilike(f"%{title}%"))
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_day(self, date: str) -> list[Lesson]:
        """Get lessons for a specific day."""
        query = select(Lesson).where()
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_weekday(self, day_of_week: int) -> list[Lesson]:
        """Get lessons for a specific day of the week (0=Monday, 6=Sunday)."""
        query = select(Lesson).where()
        result = await self.session.execute(query)
        return list(result.scalars().all())
