from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.events.validators import (
    EventExistsValidator,
    EventValidator,
    default_event_validator,
    exists_event_validator,
)


async def validated_event_data(
    session: Annotated[AsyncSession, Depends(get_db)],  # noqa: ARG001
    validator: Annotated[EventValidator, Depends(lambda: default_event_validator)],
):
    return validator


ValidatedEvent = Annotated[EventValidator, Depends(validated_event_data)]
ValidatedEventExists = Annotated[EventExistsValidator, Depends(lambda: exists_event_validator)]
