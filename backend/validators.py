from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.service import get_user_by_id
from backend.exceptions import ValidationError
from backend.lessons.models import Event
from backend.lessons.schemas import LessonCreate


class EventValidator(ABC):
    @abstractmethod
    async def validate(self, session: AsyncSession, data: BaseModel, user_id: UUID | None = None) -> None:
        pass


class TimeValidation(EventValidator):
    async def validate(self, session: AsyncSession, data: LessonCreate, user_id: UUID | None = None) -> None:  # ty: ignore[invalid-method-override]
        if data.start >= data.end:
            raise ValidationError("Lesson start time must be before end time")

        if (data.end - data.start).total_seconds() <= 60:
            raise ValidationError("Lesson duration must be greater than one minute")


class UserExistsValidation(EventValidator):
    async def validate(self, session: AsyncSession, data: LessonCreate, user_id: UUID | None = None) -> None:  # ty: ignore[invalid-method-override]
        if user_id is None:
            user_id = data.user_id
        student = await get_user_by_id(session, user_id)
        if not student:
            raise ValidationError(f"Student with ID {user_id} does not exist")

        teacher = await get_user_by_id(session, user_id)
        if not teacher:
            raise ValidationError(f"Teacher with ID {user_id} does not exist")


class NoConflictValidation(EventValidator):
    async def validate(self, session: AsyncSession, data: LessonCreate, user_id: UUID | None = None) -> None:  # ty: ignore[invalid-method-override]
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
