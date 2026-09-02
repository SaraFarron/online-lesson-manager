from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.breaks.validators import (
    default_break_validator,
    exists_break_validator,
)
from backend.database import get_db
from backend.events.validators import (
    EventExistsValidator,
    EventValidator,
)


async def validated_break_data(
    session: Annotated[AsyncSession, Depends(get_db)],  # noqa: ARG001
    validator: Annotated[EventValidator, Depends(lambda: default_break_validator)],
) -> EventValidator:
    return validator


ValidatedBreak = Annotated[EventValidator, Depends(validated_break_data)]
ValidatedBreakExists = Annotated[EventExistsValidator, Depends(lambda: exists_break_validator)]
