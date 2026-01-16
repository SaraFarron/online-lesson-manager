from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Event, User
from app.repositories.lesson import LessonRepository
from app.schemas.lesson import LessonCreate, LessonMove


class LessonService:
    """
    Service layer for Lesson business logic.

    Handles all business rules and orchestrates repository calls.
    """

    def __init__(self, session: AsyncSession):
        self.repository = LessonRepository(session)

    async def get_lessons_by_day(self, day: str, user: User) -> list[Event]:
        """Get lessons for a specific day."""
        date = datetime.strptime(day, settings.date_format).date()
        return await self.repository.get_by_day(date, user)

    async def get_lessons_by_weekday(self, day_of_week: int, user: User) -> list[Event]:
        """Get lessons for a specific day of the week (0=Monday, 6=Sunday)."""
        return await self.repository.get_by_weekday(day_of_week, user)

    async def create_recurrent_lesson(self, lesson_data: LessonCreate) -> Event:
        """Create a new recurrent lesson."""
        return await self.repository.create(lesson_data.model_dump())

    async def get_lesson(self, lesson_id: int) -> Event | None:
        """Get a lesson by ID."""
        return await self.repository.get_by_id(lesson_id)

    async def get_lessons(
        self, skip: int = 0, limit: int = 100
    ) -> list[Event]:
        """Get all lessons with pagination."""
        return await self.repository.get_all(skip=skip, limit=limit)

    async def get_published_lessons(
        self, skip: int = 0, limit: int = 100
    ) -> list[Event]:
        """Get only published lessons."""
        return await self.repository.get_published(skip=skip, limit=limit)

    async def create_lesson(self, lesson_data: LessonCreate) -> Event:
        """Create a new lesson."""
        return await self.repository.create(lesson_data.model_dump())

    async def update_lesson(
        self, lesson_id: int, lesson_data: LessonMove
    ) -> Event | None:
        """Update an existing lesson."""
        lesson = await self.repository.get_by_id(lesson_id)
        if not lesson:
            return None
        return await self.repository.update(
            lesson, lesson_data.model_dump(exclude_unset=True)
        )

    async def delete_lesson(self, lesson_id: int) -> bool:
        """Delete a lesson. Returns True if deleted, False if not found."""
        lesson = await self.repository.get_by_id(lesson_id)
        if not lesson:
            return False
        await self.repository.delete(lesson)
        return True

    async def search_lessons(self, title: str) -> list[Event]:
        """Search lessons by title."""
        return await self.repository.search_by_title(title)
