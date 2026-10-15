from abc import ABC, abstractmethod
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.service import get_user_by_id
from backend.constants import EventType
from backend.events.models import Event, RecurrentEvent
from backend.events.schemas import EventWrite, RecurrentEventWrite
from backend.events.time_utils import to_storage_utc
from backend.exceptions import ValidationError

T = TypeVar("T", bound=BaseModel)


class EventSubValidator(ABC):
    def __init__(self, event_type: str = "Event"):
        self.EVENT_TYPE = event_type

    @abstractmethod
    async def validate(
        self,
        session: AsyncSession,
        data: T,
        teacher_id: UUID,
        user_id: UUID | None = None,
        exclude_event_id: UUID | None = None,
    ) -> None:
        pass


class TimeValidation(EventSubValidator):
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
        exclude_event_id: UUID | None = None,  # noqa: ARG001
    ) -> None:
        if data.start >= data.end:
            raise ValidationError(
                f"{self.EVENT_TYPE} start time must be before end time"
            )

        if (data.end - data.start).total_seconds() <= 60:
            raise ValidationError(
                f"{self.EVENT_TYPE} duration must be greater than one minute"
            )


class UserExistsValidation(EventSubValidator):
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
        exclude_event_id: UUID | None = None,  # noqa: ARG001
    ) -> None:
        if user_id is None:
            user_id = data.user_id
        student = await get_user_by_id(session, user_id)
        if not student:
            raise ValidationError(f"Student with ID {user_id} does not exist")

        teacher = await get_user_by_id(session, teacher_id)
        if not teacher:
            raise ValidationError(f"Teacher with ID {teacher_id} does not exist")


class NoConflictValidation(EventSubValidator):
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
        exclude_event_id: UUID | None = None,
    ) -> None:
        if user_id is None:
            user_id = data.user_id
        start = to_storage_utc(data.start)
        end = to_storage_utc(data.end)
        conditions = [
            Event.teacher_id == teacher_id,
            or_(
                and_(Event.start < end, Event.start >= start),
                and_(Event.end > start, Event.end <= end),
                and_(Event.start <= start, Event.end >= end),
            ),
        ]
        if exclude_event_id is not None:
            conditions.append(Event.id != exclude_event_id)
        conflict = await session.scalar(select(Event).where(and_(*conditions)))
        if conflict:
            raise ValidationError("Scheduling conflict")

        recurrent_conditions = [
            RecurrentEvent.teacher_id == teacher_id,
            RecurrentEvent.interval_end >= start,
            or_(
                and_(RecurrentEvent.start < end, RecurrentEvent.start >= start),
                and_(RecurrentEvent.end > start, RecurrentEvent.end <= end),
                and_(RecurrentEvent.start <= start, RecurrentEvent.end >= end),
            ),
        ]
        if exclude_event_id is not None:
            recurrent_conditions.append(RecurrentEvent.id != exclude_event_id)
        recurrent_conflict = await session.scalar(
            select(RecurrentEvent).where(and_(*recurrent_conditions))
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
        user_id: UUID | None = None,
        exclude_event_id: UUID | None = None,
    ) -> None:
        if user_id is None:
            user_id = data.user_id
        start = to_storage_utc(data.start)
        interval_end = to_storage_utc(data.interval_end)
        conditions = [
            RecurrentEvent.teacher_id == teacher_id,
            or_(
                and_(
                    RecurrentEvent.start < interval_end,
                    RecurrentEvent.start >= start,
                ),
                and_(
                    RecurrentEvent.end > start,
                    RecurrentEvent.end <= interval_end,
                ),
                and_(
                    RecurrentEvent.start <= start,
                    RecurrentEvent.end >= interval_end,
                ),
            ),
        ]
        if exclude_event_id is not None:
            conditions.append(RecurrentEvent.id != exclude_event_id)
        conflict = await session.scalar(select(RecurrentEvent).where(and_(*conditions)))
        if conflict:
            raise ValidationError("Scheduling conflict with recurrent event")


class EventValidator:
    def __init__(self, event_type: str = "Event"):
        self.validators: list[EventSubValidator] = [
            TimeValidation(event_type=event_type),
            UserExistsValidation(event_type=event_type),
            NoConflictValidation(event_type=event_type),
        ]

    async def validate_all(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
        exclude_event_id: UUID | None = None,
    ):
        for validator in self.validators:
            if isinstance(data, RecurrentEventWrite) and hasattr(
                validator, "validate_recurrent"
            ):
                await validator.validate_recurrent(  # ty: ignore[call-non-callable]
                    session,
                    data,
                    teacher_id=teacher_id,
                    user_id=user_id,
                    exclude_event_id=exclude_event_id,
                )
            else:
                await validator.validate(  # ty: ignore[invalid-argument-type]
                    session,
                    data,
                    teacher_id=teacher_id,
                    user_id=user_id,
                    exclude_event_id=exclude_event_id,
                )


class EventExistsValidator:
    def __init__(
        self,
        event_type: str = "Event",
        expected_event_type: EventType | None = None,
    ):
        self.EVENT_TYPE = event_type
        self.expected_event_type = expected_event_type

    async def validate(self, session: AsyncSession, event_id: UUID):
        event = await session.get(Event, event_id)
        if event is None or (
            self.expected_event_type is not None
            and event.event_type != self.expected_event_type.value
        ):
            raise ValidationError(f"{self.EVENT_TYPE} not found")
        return event

    async def validate_recurrent(self, session: AsyncSession, event_id: UUID):
        recurrent_event = await session.get(RecurrentEvent, event_id)
        if recurrent_event is None or (
            self.expected_event_type is not None
            and recurrent_event.event_type != self.expected_event_type.value
        ):
            raise ValidationError(f"{self.EVENT_TYPE} not found")
        return recurrent_event


default_event_validator = EventValidator()
exists_event_validator = EventExistsValidator()
