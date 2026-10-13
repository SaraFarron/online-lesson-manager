import uuid
from typing import Any

from fastapi import APIRouter, status

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
from backend.events import service
from backend.events.utils import (
    assert_can_assign_user,
    assert_owns_event,
    resolve_teacher_id,
)

router = APIRouter(prefix="/breaks", tags=["breaks"])


@router.post(
    "/",
    response_model=BreakPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new break",
)
async def create_break(
    session: SessionDep, user: CurrentUser, data: BreakCreate, validator: ValidatedBreak
) -> Any:
    teacher_id = resolve_teacher_id(user)
    await assert_can_assign_user(session, user, data.user_id)
    brk = await service.create_event(session, data, validator, teacher_id=teacher_id)
    return BreakPublic.model_validate(brk)


@router.put(
    "/{break_id}",
    response_model=BreakPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing break",
)
async def update_break(
    session: SessionDep,
    user: CurrentUser,
    break_id: uuid.UUID,
    data: BreakUpdate,
    create_validator: ValidatedBreak,
    exist_validator: ValidatedBreakExists,
) -> Any:
    brk = await exist_validator.validate(session, break_id)
    teacher_id = resolve_teacher_id(user)
    assert_owns_event(teacher_id, brk)
    await assert_can_assign_user(session, user, data.user_id)
    updated_brk = await service.update_event(
        session, brk, data, create_validator, teacher_id=teacher_id
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
) -> None:
    brk = await validator.validate(session, break_id)
    teacher_id = resolve_teacher_id(user)
    assert_owns_event(teacher_id, brk)
    await service.delete_event(session, brk)


recurrent_router = APIRouter(prefix="/recurrent-breaks", tags=["breaks"])


@recurrent_router.post(
    "/",
    response_model=RecurrentBreakPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new recurring break",
)
async def create_recurrent_break(
    session: SessionDep,
    user: CurrentUser,
    data: RecurrentBreakCreate,
    validator: ValidatedBreak,
) -> Any:
    teacher_id = resolve_teacher_id(user)
    await assert_can_assign_user(session, user, data.user_id)
    brk = await service.create_recurrent_event(
        session, data, validator, teacher_id=teacher_id
    )
    return RecurrentBreakPublic.model_validate(brk)


@recurrent_router.put(
    "/{break_id}",
    response_model=RecurrentBreakPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing recurring break",
)
async def update_recurrent_break(
    session: SessionDep,
    user: CurrentUser,
    break_id: uuid.UUID,
    data: RecurrentBreakUpdate,
    create_validator: ValidatedBreak,
    exist_validator: ValidatedBreakExists,
) -> Any:
    brk = await exist_validator.validate_recurrent(session, break_id)
    teacher_id = resolve_teacher_id(user)
    assert_owns_event(teacher_id, brk)
    await assert_can_assign_user(session, user, data.user_id)
    updated_brk = await service.update_recurrent_event(
        session, brk, data, create_validator, teacher_id=teacher_id
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
) -> None:
    brk = await validator.validate_recurrent(session, break_id)
    teacher_id = resolve_teacher_id(user)
    assert_owns_event(teacher_id, brk)
    await service.delete_event(session, brk)
