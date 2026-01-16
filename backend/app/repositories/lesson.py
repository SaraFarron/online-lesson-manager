from datetime import date, datetime

from sqlalchemy import extract, select
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
        if user.role == User.Roles.TEACHER:
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

    async def get_by_weekday(self, day_of_week: int, user: User) -> list[Event]:
        """Get lessons for a specific day of the week (0=Monday, 6=Sunday)."""
        # Adjust cringe to SQL DOW (0=Sunday, 1=Monday, ..., 6=Saturday)
        sql_dow = day_of_week + 1 if day_of_week < 6 else 0

        if user.role == User.Roles.TEACHER:
            query = select(Event).where(
                Event.title == Event.Types.LESSON,
                extract('dow', Event.start) == sql_dow,
                Event.teacher_id == user.id,
            )
        else:
            query = select(Event).where(
                Event.title == Event.Types.LESSON,
                extract('dow', Event.start) == sql_dow,
                Event.student_id == user.id,
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())
