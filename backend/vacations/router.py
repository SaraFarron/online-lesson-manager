import uuid
from typing import Any

from fastapi import APIRouter, status

from backend.auth.dependencies import CurrentUser, SessionDep
from backend.events import service
from backend.events.utils import (
    assert_can_assign_user,
    assert_owns_event,
    resolve_teacher_id,
)
from backend.vacations.dependencies import ValidatedVacation, ValidatedVacationExists
from backend.vacations.schemas import VacationCreate, VacationPublic, VacationUpdate

router = APIRouter(prefix="/vacations", tags=["vacations"])


@router.post(
    "/",
    response_model=VacationPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new vacation",
)
async def create_vacation(
    session: SessionDep,
    user: CurrentUser,
    data: VacationCreate,
    validator: ValidatedVacation,
) -> Any:
    teacher_id = resolve_teacher_id(user)
    await assert_can_assign_user(session, user, data.user_id)
    vacation = await service.create_event(
        session, data, validator, teacher_id=teacher_id
    )
    return VacationPublic.model_validate(vacation)


@router.put(
    "/{vacation_id}",
    response_model=VacationPublic,
    status_code=status.HTTP_200_OK,
    summary="Update an existing vacation",
)
async def update_vacation(
    session: SessionDep,
    user: CurrentUser,
    vacation_id: uuid.UUID,
    data: VacationUpdate,
    create_validator: ValidatedVacation,
    exist_validator: ValidatedVacationExists,
) -> Any:
    vacation = await exist_validator.validate(session, vacation_id)
    teacher_id = resolve_teacher_id(user)
    assert_owns_event(user, vacation)
    await assert_can_assign_user(session, user, data.user_id)
    updated_vacation = await service.update_event(
        session, vacation, data, create_validator, teacher_id=teacher_id
    )
    return VacationPublic.model_validate(updated_vacation)


@router.delete(
    "/{vacation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an existing vacation",
)
async def delete_vacation(
    session: SessionDep,
    user: CurrentUser,
    vacation_id: uuid.UUID,
    validator: ValidatedVacationExists,
) -> None:
    vacation = await validator.validate(session, vacation_id)
    assert_owns_event(user, vacation)
    await service.delete_event(session, vacation)
