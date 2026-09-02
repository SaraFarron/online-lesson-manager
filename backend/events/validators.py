from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.service import get_user_by_id
from backend.events.models import Event
from backend.events.schemas import EventCreate
from backend.exceptions import ValidationError


class EventValidator(ABC):
    @abstractmethod
    async def validate(self, session: AsyncSession, data: BaseModel, user_id: UUID | None = None) -> None:
        pass


class TimeValidation(EventValidator):
    async def validate(self, session: AsyncSession, data: EventCreate, user_id: UUID | None = None) -> None:  # ty: ignore[invalid-method-override]
        if data.start >= data.end:
            raise ValidationError("Lesson start time must be before end time")

        if (data.end - data.start).total_seconds() <= 60:
            raise ValidationError("Lesson duration must be greater than one minute")


class UserExistsValidation(EventValidator):
    async def validate(self, session: AsyncSession, data: EventCreate, user_id: UUID | None = None) -> None:  # ty: ignore[invalid-method-override]
        if user_id is None:
            user_id = data.user_id
        student = await get_user_by_id(session, user_id)
        if not student:
            raise ValidationError(f"Student with ID {user_id} does not exist")

        teacher = await get_user_by_id(session, user_id)
        if not teacher:
            raise ValidationError(f"Teacher with ID {user_id} does not exist")


class NoConflictValidation(EventValidator):
    async def validate(self, session: AsyncSession, data: EventCreate, user_id: UUID | None = None) -> None:  # ty: ignore[invalid-method-override]
        if user_id is None:
            user_id = data.user_id
        conflict = await session.scalar(
            select(Event).where(
                and_(
                    or_(
                        Event.user_id == user_id,
                        Event.user_id == user_id,
                    ),
                    or_(
                        and_(Event.start < data.end, Event.start >= data.start),
                        and_(Event.end > data.start, Event.end <= data.end),
                        and_(Event.start <= data.start, Event.end >= data.end),
                    ),
                )
            )
        )
        if conflict:
            raise ValidationError("Scheduling conflict")


class EventValidator:
    def __init__(self):
        self.validators = [
            TimeValidation(),
            UserExistsValidation(),
            NoConflictValidation(),
        ]

    async def validate_all(self, session, data, user_id=None):
        for validator in self.validators:
            await validator.validate(session, data, user_id)


class EventExistsValidator:
    async def validate(self, session, event_id):
        event = await session.get(Event, event_id)
        if not event:
            raise ValidationError("Event not found")
        return event


default_event_validator = EventValidator()
exists_event_validator = EventExistsValidator()

