import uuid
from typing import Any

from fastapi import APIRouter, status

from backend.auth.dependencies import CurrentUser, SessionDep
from backend.breaks.dependencies import ValidatedBreak, ValidatedBreakExists
from backend.breaks.schemas import BreakCreate, BreakPublic, BreakUpdate
from backend.events import service

router = APIRouter(prefix="/breaks", tags=["breaks"])


@router.post(
    "/",
    response_model=BreakPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new break",
)
async def create_break(session: SessionDep, _: CurrentUser, data: BreakCreate, validator: ValidatedBreak) -> Any:
    brk = await service.create_event(session, data, validator)
    return BreakPublic.model_validate(brk)


@router.put(
    "/{break_id}",
    response_model=BreakPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing break",
)
async def update_break(
    session: SessionDep,
    _: CurrentUser,
    break_id: uuid.UUID,
    data: BreakUpdate,
    create_validator: ValidatedBreak,
    exist_validator: ValidatedBreakExists,
) -> Any:
    brk = await exist_validator.validate(session, break_id)
    updated_brk = await service.update_event(session, brk, data, create_validator)
    return BreakPublic.model_validate(updated_brk)


@router.delete(
    "/{break_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an existing break",
)
async def delete_break(
    session: SessionDep, _: CurrentUser, break_id: uuid.UUID, validator: ValidatedBreakExists,
) -> None:
    brk = await validator.validate(session, break_id)
    await service.delete_event(session, brk)
