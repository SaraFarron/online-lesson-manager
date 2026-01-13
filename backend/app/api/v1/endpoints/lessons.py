from fastapi import APIRouter, HTTPException, Path, status

from app.api.deps import DatabaseSession
from app.schemas.lesson import LessonCreate, LessonResponse, LessonUpdate
from app.services.lesson import LessonService

router = APIRouter()


@router.get("/day/{day}", response_model=list[LessonResponse])
async def get_lessons_by_day(
    db: DatabaseSession,
    day: str = Path(..., regex=r"^\d{4}-\d{2}-\d{2}$"),
) -> list[LessonResponse]:
    """Get lessons for a specific day."""
    service = LessonService(db)
    return await service.get_lessons_by_day(day)


@router.get("/weekday/{day_of_week}", response_model=list[LessonResponse])
async def get_lessons_by_weekday(
    db: DatabaseSession,
    day_of_week: int = Path(..., ge=0, le=6),
) -> list[LessonResponse]:
    """Get lessons for a specific day of the week (0=Monday, 6=Sunday)."""
    service = LessonService(db)
    return await service.get_lessons_by_weekday(day_of_week)


@router.post("/recurrent", response_model=LessonResponse, status_code=status.HTTP_201_CREATED)
async def create_recurrent_lesson(
    db: DatabaseSession,
    lesson_data: LessonCreate,
) -> LessonResponse:
    """Create a new recurrent lesson."""
    service = LessonService(db)
    return await service.create_recurrent_lesson(lesson_data)


@router.post(
    "",
    response_model=LessonResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_lesson(
    lesson_data: LessonCreate,
    db: DatabaseSession,
) -> LessonResponse:
    """Create a new lesson."""
    service = LessonService(db)
    return await service.create_lesson(lesson_data)


@router.patch("/{lesson_id}", response_model=LessonResponse)
async def move_lesson(
    lesson_id: int,
    lesson_data: LessonUpdate,
    db: DatabaseSession,
) -> LessonResponse:
    """Update an existing lesson."""
    service = LessonService(db)
    lesson = await service.update_lesson(lesson_id, lesson_data)
    if not lesson:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lesson with id {lesson_id} not found",
        )
    return lesson


@router.delete("/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(
    lesson_id: int,
    db: DatabaseSession,
) -> None:
    """Delete a lesson."""
    service = LessonService(db)
    deleted = await service.delete_lesson(lesson_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lesson with id {lesson_id} not found",
        )
