from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Row

from src.core.config import DB_DATETIME
from src.db.models import Executor, User


class BaseSchema(BaseModel):
    id: int


class RolesSchema:
    TEACHER: str = "TEACHER"
    STUDENT: str = "STUDENT"


class UserSchema(BaseSchema):
    telegram_id: int
    username: str | None
    full_name: str
    role: str
    executor_id: int

    @staticmethod
    def from_user(user: User):
        return UserSchema(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            executor_id=user.executor_id,
        )


class ExecutorSchema(BaseSchema):
    code: str
    telegram_id: int

    @staticmethod
    def from_executor(executor: Executor):
        return ExecutorSchema(id=executor.id, code=executor.code, telegram_id=executor.telegram_id)


class BaseEventSchema(BaseSchema):
    user_id: int
    executor_id: int
    event_type: str
    start: datetime
    end: datetime


class EventSchema(BaseEventSchema):
    cancelled: bool
    reschedule_id: int | None
    is_reschedule: bool

    @staticmethod
    def from_row(row: Row):
        return EventSchema(
            id=row.id,
            start=datetime.strptime(row.start, DB_DATETIME),
            end=datetime.strptime(row.end, DB_DATETIME),
            user_id=row.user_id,
            executor_id=row.executor_id,
            event_type=row.event_type,
            cancelled=row.cancelled,
            reschedule_id=row.reschedule_id,
            is_reschedule=row.is_reschedule,
        )


class RecurrentEventSchema(BaseEventSchema):
    interval: int
    interval_end: datetime | None

    @staticmethod
    def from_row(row: Row):
        return RecurrentEventSchema(
            id=row.id,
            user_id=row.user_id,
            executor_id=row.executor_id,
            event_type=row.event_type,
            start=datetime.strptime(row.start, DB_DATETIME),
            end=datetime.strptime(row.end, DB_DATETIME),
            interval=row.interval,
            interval_end=row.interval_end,
        )


class CancelledRecurrentEventSchema(BaseSchema):
    event_id: int
    break_type: str
    start: datetime
    end: datetime


class EventHistorySchema(BaseSchema):
    author: str
    scene: str
    event_type: str
    event_value: str
    created_at: datetime

    @staticmethod
    def from_row(row: Row):
        return EventHistorySchema(
            id=row.id,
            created_at=datetime.strptime(row.created_at, DB_DATETIME),
            scene=row.scene,
            event_type=row.event_type,
            event_value=row.event_value,
            author=row.author,
        )
