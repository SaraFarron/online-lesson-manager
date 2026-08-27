from sqlalchemy.ext.asyncio import AsyncSession

from backend.lessons.models import Event
from backend.lessons.schemas import LessonCreate


async def create_lesson(session: AsyncSession, lesson_data: LessonCreate) -> Event:
    lesson = Event(
        start=lesson_data.start,
        end=lesson_data.end,
        student_id=lesson_data.student_id,
        teacher_id=lesson_data.teacher_id,
    )
    session.add(lesson)
    await session.commit()
    await session.refresh(lesson)
    return lesson
