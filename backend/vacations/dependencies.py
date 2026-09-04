from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.events.validators import (
    EventExistsValidator,
    EventValidator,
)
from backend.vacations.validators import (
    default_vacation_validator,
    exists_vacation_validator,
)


async def validated_vacation_data(
    session: Annotated[AsyncSession, Depends(get_db)],  # noqa: ARG001
    validator: Annotated[EventValidator, Depends(lambda: default_vacation_validator)],
) -> EventValidator:
    return validator


ValidatedVacation = Annotated[EventValidator, Depends(validated_vacation_data)]
ValidatedVacationExists = Annotated[EventExistsValidator, Depends(lambda: exists_vacation_validator)]
