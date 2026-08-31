import uuid
from typing import Any

from fastapi import APIRouter, status

from backend.auth.dependencies import CurrentUser, SessionDep
from backend.lessons import service
from backend.lessons.dependencies import ValidatedLesson, ValidatedLessonExists
from backend.lessons.schemas import LessonCreate, LessonPublic, LessonUpdate

router = APIRouter(prefix="/lessons", tags=["lessons"])


@router.post(
    "/",
    response_model=LessonPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new lesson",
)
async def create_lesson(session: SessionDep, _: CurrentUser, data: LessonCreate, validator: ValidatedLesson) -> Any:
    lesson = await service.create_lesson(session, data, validator)
    return LessonPublic.model_validate(lesson)


@router.put(
    "/{lesson_id}",
    response_model=LessonPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing lesson",
)
async def update_lesson(
    session: SessionDep,
    _: CurrentUser,
    lesson_id: uuid.UUID,
    data: LessonUpdate,
    create_validator: ValidatedLesson,
    exist_validator: ValidatedLessonExists,
) -> Any:
    lesson = await exist_validator.validate(session, lesson_id)
    updated_lesson = await service.update_lesson(session, lesson, data, create_validator)
    return LessonPublic.model_validate(updated_lesson)


@router.delete(
    "/{lesson_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an existing lesson",
)
async def delete_lesson(
    session: SessionDep, _: CurrentUser, lesson_id: uuid.UUID, validator: ValidatedLessonExists,
) -> None:
    lesson = await validator.validate(session, lesson_id)
    await service.delete_lesson(session, lesson)
