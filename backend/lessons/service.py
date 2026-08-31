from sqlalchemy.ext.asyncio import AsyncSession

from backend.lessons.models import Event
from backend.lessons.schemas import LessonCreate
from backend.lessons.validators import LessonValidator


async def create_lesson(session: AsyncSession, lesson_data: LessonCreate, validator: LessonValidator) -> Event:
    await validator.validate_all(session, lesson_data)

    lesson = Event(
        start=lesson_data.start,
        end=lesson_data.end,
        user_id=lesson_data.user_id,
        event_type=lesson_data.event_type,
    )
    session.add(lesson)
    await session.commit()
    await session.refresh(lesson)
    return lesson
