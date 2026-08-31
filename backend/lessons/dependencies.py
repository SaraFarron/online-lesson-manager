from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.lessons.validators import LessonValidator, default_lesson_validator


async def validated_lesson_data(
    session: Annotated[AsyncSession, Depends(get_db)],  # noqa: ARG001
    validator: Annotated[LessonValidator, Depends(lambda: default_lesson_validator)],
):
    return validator


ValidatedLesson = Annotated[LessonValidator, Depends(validated_lesson_data)]
