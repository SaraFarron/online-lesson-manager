from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson import Lesson
from app.repositories.lesson import LessonRepository
from app.schemas.lesson import LessonCreate, LessonUpdate


class LessonService:
    """
    Service layer for Lesson business logic.

    Handles all business rules and orchestrates repository calls.
    """

    def __init__(self, session: AsyncSession):
        self.repository = LessonRepository(session)

    async def get_lessons_by_day(self, date: str) -> list[Lesson]:
        """Get lessons for a specific day."""
        return await self.repository.get_by_day(date)

    async def get_lessons_by_weekday(self, day_of_week: int) -> list[Lesson]:
        """Get lessons for a specific day of the week (0=Monday, 6=Sunday)."""
        return await self.repository.get_by_weekday(day_of_week)

    async def create_recurrent_lesson(self, lesson_data: LessonCreate) -> Lesson:
        """Create a new recurrent lesson."""
        return await self.repository.create(lesson_data.model_dump())

    async def get_lesson(self, lesson_id: int) -> Lesson | None:
        """Get a lesson by ID."""
        return await self.repository.get_by_id(lesson_id)

    async def get_lessons(
        self, skip: int = 0, limit: int = 100
    ) -> list[Lesson]:
        """Get all lessons with pagination."""
        return await self.repository.get_all(skip=skip, limit=limit)

    async def get_published_lessons(
        self, skip: int = 0, limit: int = 100
    ) -> list[Lesson]:
        """Get only published lessons."""
        return await self.repository.get_published(skip=skip, limit=limit)

    async def create_lesson(self, lesson_data: LessonCreate) -> Lesson:
        """Create a new lesson."""
        return await self.repository.create(lesson_data.model_dump())

    async def update_lesson(
        self, lesson_id: int, lesson_data: LessonUpdate
    ) -> Lesson | None:
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

    async def search_lessons(self, title: str) -> list[Lesson]:
        """Search lessons by title."""
        return await self.repository.search_by_title(title)
