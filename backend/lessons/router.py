import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, status

from backend.auth.constants import Roles
from backend.auth.dependencies import CurrentUser, SessionDep
from backend.constants import EventType
from backend.events import management as management_service
from backend.events import service
from backend.events.management_schemas import (
    EventManagementDetail,
    EventManagementPage,
)
from backend.events.schemas import RecurrentCancel
from backend.events.utils import (
    assert_can_assign_user,
    assert_owns_event,
    resolve_teacher_id,
)
from backend.lessons.dependencies import ValidatedLesson, ValidatedLessonExists
from backend.lessons.schemas import (
    LessonCreate,
    LessonPublic,
    LessonUpdate,
    RecurrentLessonCreate,
    RecurrentLessonPublic,
    RecurrentLessonUpdate,
    RecurrentOccurrenceActionResult,
    RecurrentOccurrenceCancelRequest,
    RecurrentOccurrenceMoveRequest,
)

router = APIRouter(prefix="/lessons", tags=["lessons"])


@router.get("/", response_model=EventManagementPage, summary="List manageable lessons")
async def list_lessons(
    session: SessionDep,
    user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    subject_id: uuid.UUID | None = None,
) -> EventManagementPage:
    return await management_service.list_managed_events(
        session,
        user,
        EventType.LESSON,
        is_recurrent=False,
        skip=skip,
        limit=limit,
        subject_id=subject_id,
    )


@router.get("/{lesson_id}", response_model=EventManagementDetail)
async def get_lesson(
    session: SessionDep, user: CurrentUser, lesson_id: uuid.UUID
) -> EventManagementDetail:
    return await management_service.get_managed_event(
        session, user, lesson_id, EventType.LESSON, is_recurrent=False
    )


@router.post(
    "/",
    response_model=LessonPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new lesson",
)
async def create_lesson(
    session: SessionDep,
    user: CurrentUser,
    data: LessonCreate,
    validator: ValidatedLesson,
) -> Any:
    teacher_id = resolve_teacher_id(user)
    await assert_can_assign_user(session, user, data.user_id)
    lesson = await service.create_event(
        session,
        data,
        validator,
        teacher_id=teacher_id,
        actor_is_teacher=user.role == Roles.TEACHER,
    )
    return LessonPublic.model_validate(lesson)


@router.put(
    "/{lesson_id}",
    response_model=LessonPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing lesson",
)
async def update_lesson(
    session: SessionDep,
    user: CurrentUser,
    lesson_id: uuid.UUID,
    data: LessonUpdate,
    create_validator: ValidatedLesson,
    exist_validator: ValidatedLessonExists,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Management revision returned by the event detail read.",
        ),
    ] = None,
) -> Any:
    lesson = await exist_validator.validate(session, lesson_id)
    teacher_id = resolve_teacher_id(user)
    assert_owns_event(user, lesson)
    await assert_can_assign_user(session, user, data.user_id)
    updated_lesson = await service.update_event(
        session,
        lesson,
        data,
        create_validator,
        teacher_id=teacher_id,
        actor=user,
        expected_revision=if_match,
        actor_is_teacher=user.role == Roles.TEACHER,
    )
    return LessonPublic.model_validate(updated_lesson)


@router.delete(
    "/{lesson_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an existing lesson",
)
async def delete_lesson(
    session: SessionDep,
    user: CurrentUser,
    lesson_id: uuid.UUID,
    validator: ValidatedLessonExists,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Management revision returned by the event detail read.",
        ),
    ] = None,
) -> None:
    lesson = await validator.validate(session, lesson_id)
    assert_owns_event(user, lesson)
    await service.delete_event(session, lesson, actor=user, expected_revision=if_match)


recurrent_router = APIRouter(prefix="/recurrent-lessons", tags=["lessons"])


@recurrent_router.get(
    "/", response_model=EventManagementPage, summary="List manageable recurring lessons"
)
async def list_recurrent_lessons(
    session: SessionDep,
    user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    subject_id: uuid.UUID | None = None,
) -> EventManagementPage:
    return await management_service.list_managed_events(
        session,
        user,
        EventType.LESSON,
        is_recurrent=True,
        skip=skip,
        limit=limit,
        subject_id=subject_id,
    )


@recurrent_router.get("/{lesson_id}", response_model=EventManagementDetail)
async def get_recurrent_lesson(
    session: SessionDep,
    user: CurrentUser,
    lesson_id: uuid.UUID,
    occurrence_start: datetime | None = None,
) -> EventManagementDetail:
    return await management_service.get_managed_event(
        session,
        user,
        lesson_id,
        EventType.LESSON,
        is_recurrent=True,
        occurrence_start=occurrence_start,
    )


@recurrent_router.post(
    "/{lesson_id}/occurrences/cancel",
    response_model=RecurrentOccurrenceActionResult,
    summary="Cancel one exact occurrence of a recurring lesson",
)
async def cancel_recurrent_lesson_occurrence(
    session: SessionDep,
    user: CurrentUser,
    lesson_id: uuid.UUID,
    data: RecurrentOccurrenceCancelRequest,
) -> RecurrentOccurrenceActionResult:
    teacher_id = resolve_teacher_id(user)
    cancellation, revision = await service.cancel_recurrent_lesson_occurrence(
        session, lesson_id, data, user, teacher_id
    )
    return RecurrentOccurrenceActionResult(
        cancelled_occurrence=RecurrentCancel(
            start=cancellation.start, end=cancellation.end
        ),
        revision=revision,
    )


@recurrent_router.post(
    "/{lesson_id}/occurrences/move",
    response_model=RecurrentOccurrenceActionResult,
    status_code=status.HTTP_201_CREATED,
    summary="Move one exact occurrence to a one-off lesson",
)
async def move_recurrent_lesson_occurrence(
    session: SessionDep,
    user: CurrentUser,
    lesson_id: uuid.UUID,
    data: RecurrentOccurrenceMoveRequest,
    validator: ValidatedLesson,
) -> RecurrentOccurrenceActionResult:
    teacher_id = resolve_teacher_id(user)
    (
        cancellation,
        replacement,
        revision,
    ) = await service.move_recurrent_lesson_occurrence(
        session, lesson_id, data, user, teacher_id, validator
    )
    return RecurrentOccurrenceActionResult(
        cancelled_occurrence=RecurrentCancel(
            start=cancellation.start, end=cancellation.end
        ),
        replacement=LessonPublic.model_validate(replacement),
        revision=revision,
    )


@recurrent_router.post(
    "/",
    response_model=RecurrentLessonPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new recurring lesson",
)
async def create_recurrent_lesson(
    session: SessionDep,
    user: CurrentUser,
    data: RecurrentLessonCreate,
    validator: ValidatedLesson,
) -> Any:
    teacher_id = resolve_teacher_id(user)
    await assert_can_assign_user(session, user, data.user_id)
    lesson = await service.create_recurrent_event(
        session,
        data,
        validator,
        teacher_id=teacher_id,
        actor_is_teacher=user.role == Roles.TEACHER,
    )
    return RecurrentLessonPublic.model_validate(lesson)


@recurrent_router.put(
    "/{lesson_id}",
    response_model=RecurrentLessonPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing recurring lesson",
)
async def update_recurrent_lesson(
    session: SessionDep,
    user: CurrentUser,
    lesson_id: uuid.UUID,
    data: RecurrentLessonUpdate,
    create_validator: ValidatedLesson,
    exist_validator: ValidatedLessonExists,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Management revision returned by the event detail read.",
        ),
    ] = None,
) -> Any:
    lesson = await exist_validator.validate_recurrent(session, lesson_id)
    teacher_id = resolve_teacher_id(user)
    assert_owns_event(user, lesson)
    await assert_can_assign_user(session, user, data.user_id)
    updated_lesson = await service.update_recurrent_event(
        session,
        lesson,
        data,
        create_validator,
        teacher_id=teacher_id,
        actor=user,
        expected_revision=if_match,
        actor_is_teacher=user.role == Roles.TEACHER,
    )
    return RecurrentLessonPublic.model_validate(updated_lesson)


@recurrent_router.delete(
    "/{lesson_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an existing recurring lesson",
)
async def delete_recurrent_lesson(
    session: SessionDep,
    user: CurrentUser,
    lesson_id: uuid.UUID,
    validator: ValidatedLessonExists,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Management revision returned by the event detail read.",
        ),
    ] = None,
) -> None:
    lesson = await validator.validate_recurrent(session, lesson_id)
    assert_owns_event(user, lesson)
    await service.delete_event(session, lesson, actor=user, expected_revision=if_match)
