from abc import ABC, abstractmethod
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.service import get_user_by_id
from backend.events.models import Event, RecurrentEvent
from backend.events.schemas import EventWrite, RecurrentEventWrite
from backend.exceptions import ValidationError

T = TypeVar("T", bound=BaseModel)


class EventValidator(ABC):
    def __init__(self, event_type: str = "Event"):
        self.EVENT_TYPE = event_type

    @abstractmethod
    async def validate(
        self, session: AsyncSession, data: T, teacher_id: UUID, user_id: UUID | None = None
    ) -> None:
        pass


class TimeValidation(EventValidator):
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
    ) -> None:
        if data.start >= data.end:
            raise ValidationError(f"{self.EVENT_TYPE} start time must be before end time")

        if (data.end - data.start).total_seconds() <= 60:
            raise ValidationError(f"{self.EVENT_TYPE} duration must be greater than one minute")


class UserExistsValidation(EventValidator):
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
    ) -> None:
        if user_id is None:
            user_id = data.user_id
        student = await get_user_by_id(session, user_id)
        if not student:
            raise ValidationError(f"Student with ID {user_id} does not exist")

        teacher = await get_user_by_id(session, teacher_id)
        if not teacher:
            raise ValidationError(f"Teacher with ID {teacher_id} does not exist")


class NoConflictValidation(EventValidator):
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None
    ) -> None:
        if user_id is None:
            user_id = data.user_id
        conflict = await session.scalar(
            select(Event).where(
                and_(
                    or_(
                        Event.teacher_id == teacher_id,
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

        recurrent_conflict = await session.scalar(
            select(RecurrentEvent).where(
                and_(
                    RecurrentEvent.teacher_id == teacher_id,
                    RecurrentEvent.interval_end >= data.start,
                    or_(
                        and_(RecurrentEvent.start < data.end, RecurrentEvent.start >= data.start),
                        and_(RecurrentEvent.end > data.start, RecurrentEvent.end <= data.end),
                        and_(RecurrentEvent.start <= data.start, RecurrentEvent.end >= data.end),
                    ),
                )
            )
        )
        if recurrent_conflict:
            day_diff = (data.start.date() - recurrent_conflict.start.date()).days
            if day_diff >= 0 and day_diff % recurrent_conflict.interval == 0:
                raise ValidationError("Scheduling conflict with recurrent event")

    async def validate_recurrent(
        self,
        session: AsyncSession,
        data: RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None
    ) -> None:
        if user_id is None:
            user_id = data.user_id
        conflict = await session.scalar(
            select(RecurrentEvent).where(
                and_(
                    RecurrentEvent.teacher_id == teacher_id,
                    or_(
                        and_(RecurrentEvent.start < data.interval_end, RecurrentEvent.start >= data.start),
                        and_(RecurrentEvent.end > data.start, RecurrentEvent.end <= data.interval_end),
                        and_(RecurrentEvent.start <= data.start, RecurrentEvent.end >= data.interval_end),
                    ),
                )
            )
        )
        if conflict:
            raise ValidationError("Scheduling conflict with recurrent event")


class EventValidator:
    def __init__(self, event_type: str = "Event"):
        self.validators = [
            TimeValidation(event_type=event_type),
            UserExistsValidation(event_type=event_type),
            NoConflictValidation(event_type=event_type),
        ]

    async def validate_all(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None
    ):
        for validator in self.validators:
            if isinstance(data, RecurrentEventWrite) and hasattr(validator, "validate_recurrent"):
                await validator.validate_recurrent(session, data, teacher_id=teacher_id, user_id=user_id)
            else:
                await validator.validate(session, data, teacher_id=teacher_id, user_id=user_id)  # ty: ignore[invalid-argument-type]


class EventExistsValidator:
    def __init__(self, event_type: str = "Event"):
        self.EVENT_TYPE = event_type

    async def validate(self, session: AsyncSession, event_id: UUID):
        event = await session.get(Event, event_id)
        if not event:
            raise ValidationError(f"{self.EVENT_TYPE} not found")
        return event


default_event_validator = EventValidator()
exists_event_validator = EventExistsValidator()
