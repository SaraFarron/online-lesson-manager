from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.events.validators import (
    EventExistsValidator,
    EventValidator,
)
from backend.lessons.validators import (
    default_lesson_validator,
    exists_lesson_validator,
)


async def validated_lesson_data(
    session: Annotated[AsyncSession, Depends(get_db)],  # noqa: ARG001
    validator: Annotated[EventValidator, Depends(lambda: default_lesson_validator)],
) -> EventValidator:
    return validator


ValidatedLesson = Annotated[EventValidator, Depends(validated_lesson_data)]
ValidatedLessonExists = Annotated[
    EventExistsValidator, Depends(lambda: exists_lesson_validator)
]
