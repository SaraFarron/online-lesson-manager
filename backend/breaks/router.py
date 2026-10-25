import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, status

from backend.auth.dependencies import CurrentUser, SessionDep
from backend.breaks.dependencies import ValidatedBreak, ValidatedBreakExists
from backend.breaks.schemas import (
    BreakCreate,
    BreakPublic,
    BreakUpdate,
    RecurrentBreakCreate,
    RecurrentBreakPublic,
    RecurrentBreakUpdate,
)
from backend.constants import EventType
from backend.events import management as management_service
from backend.events import service
from backend.events.management_schemas import (
    EventManagementDetail,
    EventManagementPage,
)
from backend.events.utils import (
    assert_can_assign_user,
    assert_owns_event,
    assert_teacher_actor,
    resolve_teacher_id,
)

router = APIRouter(prefix="/breaks", tags=["breaks"])


@router.get("/", response_model=EventManagementPage, summary="List manageable breaks")
async def list_breaks(
    session: SessionDep,
    user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    subject_id: uuid.UUID | None = None,
) -> EventManagementPage:
    return await management_service.list_managed_events(
        session,
        user,
        EventType.BREAK,
        is_recurrent=False,
        skip=skip,
        limit=limit,
        subject_id=subject_id,
    )


@router.get("/{break_id}", response_model=EventManagementDetail)
async def get_break(
    session: SessionDep, user: CurrentUser, break_id: uuid.UUID
) -> EventManagementDetail:
    return await management_service.get_managed_event(
        session, user, break_id, EventType.BREAK, is_recurrent=False
    )


@router.post(
    "/",
    response_model=BreakPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new break",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": (
                "Non-mutating preview of lesson occurrences and a confirmation token. "
                "Retry with confirm_displacement=true and the preview token in "
                "X-Displacement-Token; the server rechecks the request and affected set."
            )
        }
    },
)
async def create_break(
    session: SessionDep,
    user: CurrentUser,
    data: BreakCreate,
    validator: ValidatedBreak,
    confirm_displacement: bool = Query(
        default=False,
        description="Confirm cancellation of overlapping lesson occurrences.",
    ),
    displacement_token: Annotated[
        str | None,
        Header(
            alias="X-Displacement-Token",
            description="Token returned by the latest displacement preview.",
        ),
    ] = None,
) -> Any:
    assert_teacher_actor(user)
    teacher_id = resolve_teacher_id(user)
    await assert_can_assign_user(session, user, data.user_id)
    brk = await service.create_event(
        session,
        data,
        validator,
        teacher_id=teacher_id,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
    )
    return BreakPublic.model_validate(brk)


@router.put(
    "/{break_id}",
    response_model=BreakPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing break",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": (
                "Non-mutating preview of lesson occurrences and a confirmation token. "
                "Retry with confirm_displacement=true and the preview token in "
                "X-Displacement-Token; the server rechecks the request and affected set."
            )
        }
    },
)
async def update_break(
    session: SessionDep,
    user: CurrentUser,
    break_id: uuid.UUID,
    data: BreakUpdate,
    create_validator: ValidatedBreak,
    exist_validator: ValidatedBreakExists,
    confirm_displacement: bool = Query(
        default=False,
        description="Confirm cancellation of overlapping lesson occurrences.",
    ),
    displacement_token: Annotated[
        str | None,
        Header(
            alias="X-Displacement-Token",
            description="Token returned by the latest displacement preview.",
        ),
    ] = None,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Management revision returned by the event detail read.",
        ),
    ] = None,
) -> Any:
    assert_teacher_actor(user)
    teacher_id = resolve_teacher_id(user)
    brk = await exist_validator.validate(session, break_id, teacher_id=teacher_id)
    assert_owns_event(user, brk)
    await assert_can_assign_user(session, user, data.user_id)
    updated_brk = await service.update_event(
        session,
        brk,
        data,
        create_validator,
        teacher_id=teacher_id,
        actor=user,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
        expected_revision=if_match,
    )
    return BreakPublic.model_validate(updated_brk)


@router.delete(
    "/{break_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an existing break",
)
async def delete_break(
    session: SessionDep,
    user: CurrentUser,
    break_id: uuid.UUID,
    validator: ValidatedBreakExists,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Management revision returned by the event detail read.",
        ),
    ] = None,
) -> None:
    assert_teacher_actor(user)
    teacher_id = resolve_teacher_id(user)
    brk = await validator.validate(session, break_id, teacher_id=teacher_id)
    assert_owns_event(user, brk)
    await service.delete_event(session, brk, actor=user, expected_revision=if_match)


recurrent_router = APIRouter(prefix="/recurrent-breaks", tags=["breaks"])


@recurrent_router.get(
    "/",
    response_model=EventManagementPage,
    summary="List manageable recurring breaks",
)
async def list_recurrent_breaks(
    session: SessionDep,
    user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    subject_id: uuid.UUID | None = None,
) -> EventManagementPage:
    return await management_service.list_managed_events(
        session,
        user,
        EventType.BREAK,
        is_recurrent=True,
        skip=skip,
        limit=limit,
        subject_id=subject_id,
    )


@recurrent_router.get("/{break_id}", response_model=EventManagementDetail)
async def get_recurrent_break(
    session: SessionDep,
    user: CurrentUser,
    break_id: uuid.UUID,
    occurrence_start: datetime | None = None,
) -> EventManagementDetail:
    return await management_service.get_managed_event(
        session,
        user,
        break_id,
        EventType.BREAK,
        is_recurrent=True,
        occurrence_start=occurrence_start,
    )


@recurrent_router.post(
    "/",
    response_model=RecurrentBreakPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new recurring break",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": (
                "Non-mutating preview of lesson occurrences and a confirmation token. "
                "Retry with confirm_displacement=true and the preview token in "
                "X-Displacement-Token; the server rechecks the request and affected set."
            )
        }
    },
)
async def create_recurrent_break(
    session: SessionDep,
    user: CurrentUser,
    data: RecurrentBreakCreate,
    validator: ValidatedBreak,
    confirm_displacement: bool = Query(
        default=False,
        description="Confirm cancellation of overlapping lesson occurrences.",
    ),
    displacement_token: Annotated[
        str | None,
        Header(
            alias="X-Displacement-Token",
            description="Token returned by the latest displacement preview.",
        ),
    ] = None,
) -> Any:
    assert_teacher_actor(user)
    teacher_id = resolve_teacher_id(user)
    await assert_can_assign_user(session, user, data.user_id)
    brk = await service.create_recurrent_event(
        session,
        data,
        validator,
        teacher_id=teacher_id,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
    )
    return RecurrentBreakPublic.model_validate(brk)


@recurrent_router.put(
    "/{break_id}",
    response_model=RecurrentBreakPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing recurring break",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": (
                "Non-mutating preview of lesson occurrences and a confirmation token. "
                "Retry with confirm_displacement=true and the preview token in "
                "X-Displacement-Token; the server rechecks the request and affected set."
            )
        }
    },
)
async def update_recurrent_break(
    session: SessionDep,
    user: CurrentUser,
    break_id: uuid.UUID,
    data: RecurrentBreakUpdate,
    create_validator: ValidatedBreak,
    exist_validator: ValidatedBreakExists,
    confirm_displacement: bool = Query(
        default=False,
        description="Confirm cancellation of overlapping lesson occurrences.",
    ),
    displacement_token: Annotated[
        str | None,
        Header(
            alias="X-Displacement-Token",
            description="Token returned by the latest displacement preview.",
        ),
    ] = None,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Management revision returned by the event detail read.",
        ),
    ] = None,
) -> Any:
    assert_teacher_actor(user)
    teacher_id = resolve_teacher_id(user)
    brk = await exist_validator.validate_recurrent(
        session, break_id, teacher_id=teacher_id
    )
    assert_owns_event(user, brk)
    await assert_can_assign_user(session, user, data.user_id)
    updated_brk = await service.update_recurrent_event(
        session,
        brk,
        data,
        create_validator,
        teacher_id=teacher_id,
        actor=user,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
        expected_revision=if_match,
    )
    return RecurrentBreakPublic.model_validate(updated_brk)


@recurrent_router.delete(
    "/{break_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an existing recurring break",
)
async def delete_recurrent_break(
    session: SessionDep,
    user: CurrentUser,
    break_id: uuid.UUID,
    validator: ValidatedBreakExists,
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Management revision returned by the event detail read.",
        ),
    ] = None,
) -> None:
    assert_teacher_actor(user)
    teacher_id = resolve_teacher_id(user)
    brk = await validator.validate_recurrent(session, break_id, teacher_id=teacher_id)
    assert_owns_event(user, brk)
    await service.delete_event(session, brk, actor=user, expected_revision=if_match)
