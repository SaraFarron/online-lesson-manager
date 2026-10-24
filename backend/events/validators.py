from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.service import get_user_by_id
from backend.constants import EventType
from backend.events.booking_policy import TeacherBookingPreferences
from backend.events.conflicts import validate_schedule_change
from backend.events.models import Event, RecurrentEvent
from backend.events.occurrences import Occurrence
from backend.events.schemas import (
    EventWrite,
    RecurrentEventWrite,
)
from backend.exceptions import ValidationError


class EventSubValidator(ABC):
    def __init__(self, event_type: str = "Event"):
        self.EVENT_TYPE = event_type

    @abstractmethod
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
    ) -> None:  # noqa: ARG002
        pass


class TimeValidation(EventSubValidator):
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
    ) -> None:
        if data.end is None:
            raise ValidationError(f"{self.EVENT_TYPE} end time is required")
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
    ) -> None:
        if user_id is None:
            user_id = data.user_id
        student = await get_user_by_id(session, user_id)
        if not student:
            raise ValidationError(f"Student with ID {user_id} does not exist")

        teacher = await get_user_by_id(session, teacher_id)
        if not teacher:
            raise ValidationError(f"Teacher with ID {teacher_id} does not exist")


class NoConflictValidation:
    async def validate(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        exclude_event_id: UUID | None = None,
        *,
        expected_event_type: EventType | None = None,
        recurrence_timezone: str | None = None,
        recurrence_mode: str | None = None,
        canceled_starts: Sequence[datetime] = (),
        confirm_displacement: bool = False,
        displacement_token: str | None = None,
        lesson_preferences: TeacherBookingPreferences | None = None,
        teacher_timezone: str | None = None,
    ) -> list[Occurrence]:
        return await validate_schedule_change(
            session,
            data,
            teacher_id,
            expected_event_type=expected_event_type,
            exclude_event_id=exclude_event_id,
            recurrence_timezone=recurrence_timezone,
            recurrence_mode=recurrence_mode,
            canceled_starts=canceled_starts,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
            lesson_preferences=lesson_preferences,
            teacher_timezone=teacher_timezone,
        )


class EventValidator:
    def __init__(
        self,
        event_type: str = "Event",
        expected_event_type: EventType | None = None,
    ):
        self.expected_event_type = expected_event_type
        self.validators: list[EventSubValidator] = [
            TimeValidation(event_type=event_type),
            UserExistsValidation(event_type=event_type),
        ]
        self.conflict_validator = NoConflictValidation()

    async def validate_all(
        self,
        session: AsyncSession,
        data: EventWrite | RecurrentEventWrite,
        teacher_id: UUID,
        user_id: UUID | None = None,
        exclude_event_id: UUID | None = None,
        *,
        recurrence_timezone: str | None = None,
        recurrence_mode: str | None = None,
        canceled_starts: Sequence[datetime] = (),
        confirm_displacement: bool = False,
        displacement_token: str | None = None,
        teacher_profile: TeacherBookingPreferences | None = None,
        teacher_timezone: str | None = None,
    ) -> list[Occurrence]:
        for validator in self.validators:
            await validator.validate(
                session, data, teacher_id=teacher_id, user_id=user_id
            )
        displaced = await self.conflict_validator.validate(
            session,
            data,
            teacher_id,
            exclude_event_id,
            expected_event_type=self.expected_event_type,
            recurrence_timezone=recurrence_timezone,
            recurrence_mode=recurrence_mode,
            canceled_starts=canceled_starts,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
            lesson_preferences=teacher_profile,
            teacher_timezone=teacher_timezone,
        )
        return displaced


class EventExistsValidator:
    def __init__(
        self,
        event_type: str = "Event",
        expected_event_type: EventType | None = None,
    ):
        self.EVENT_TYPE = event_type
        self.expected_event_type = expected_event_type

    async def validate(self, session: AsyncSession, event_id: UUID) -> Event:
        event = await session.get(Event, event_id)
        if event is None or (
            self.expected_event_type is not None
            and event.event_type != self.expected_event_type.value
        ):
            raise ValidationError(f"{self.EVENT_TYPE} not found")
        return event

    async def validate_recurrent(
        self, session: AsyncSession, event_id: UUID
    ) -> RecurrentEvent:
        recurrent_event = await session.get(RecurrentEvent, event_id)
        if recurrent_event is None or (
            self.expected_event_type is not None
            and recurrent_event.event_type != self.expected_event_type.value
        ):
            raise ValidationError(f"{self.EVENT_TYPE} not found")
        return recurrent_event


default_event_validator = EventValidator()
exists_event_validator = EventExistsValidator()
