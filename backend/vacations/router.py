import uuid
from typing import Any

from fastapi import APIRouter, status

from backend.auth.dependencies import CurrentUser, SessionDep
from backend.events import service
from backend.events.dependencies import ValidatedEvent, ValidatedEventExists
from backend.vacations.schemas import VacationCreate, VacationPublic, VacationUpdate

router = APIRouter(prefix="/vacations", tags=["vacations"])


@router.post(
    "/",
    response_model=VacationPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new vacation",
)
async def create_vacation(session: SessionDep, _: CurrentUser, data: VacationCreate, validator: ValidatedEvent) -> Any:
    vacation = await service.create_event(session, data, validator)
    return VacationPublic.model_validate(vacation)


@router.put(
    "/{vacation_id}",
    response_model=VacationPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing vacation",
)
async def update_vacation(
    session: SessionDep,
    _: CurrentUser,
    vacation_id: uuid.UUID,
    data: VacationUpdate,
    create_validator: ValidatedEvent,
    exist_validator: ValidatedEventExists,
) -> Any:
    vacation = await exist_validator.validate(session, vacation_id)
    updated_vacation = await service.update_event(session, vacation, data, create_validator)
    return VacationPublic.model_validate(updated_vacation)


@router.delete(
    "/{vacation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an existing vacation",
)
async def delete_vacation(
    session: SessionDep, _: CurrentUser, vacation_id: uuid.UUID, validator: ValidatedEventExists,
) -> None:
    vacation = await validator.validate(session, vacation_id)
    await service.delete_event(session, vacation)
