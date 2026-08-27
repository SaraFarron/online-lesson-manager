from typing import Any

from fastapi import APIRouter, status

from backend.auth.dependencies import CurrentUser, SessionDep
from backend.lessons import service
from backend.lessons.schemas import LessonCreate, LessonPublic

router = APIRouter(prefix="/lessons", tags=["lessons"])


@router.post(
    "/",
    response_model=LessonPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new lesson",
)
async def create_lesson(session: SessionDep, _: CurrentUser, data: LessonCreate) -> Any:
    lesson = await service.create_lesson(session, data)
    return LessonPublic.model_validate(lesson)
