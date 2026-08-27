import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_serializer,
    field_validator,
)

from backend.auth.constants import Roles

# ── User ──────────────────────────────────────────────────────────────────────


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=256)
    is_active: bool = True
    is_superuser: bool = False


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)
    role: Roles
    timezone: str

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError(f"Invalid timezone: {v}")
        return v


class UserUpdate(BaseModel):
    """Admin: update any user field."""

    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
    is_superuser: bool | None = None


class UserUpdateMe(BaseModel):
    """Self-service profile update — cannot escalate privileges."""

    full_name: str | None = Field(default=None, max_length=256)
    email: EmailStr | None = None


class UserPublic(UserBase):
    id: uuid.UUID
    timezone: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserList(BaseModel):
    items: list[UserPublic]
    total: int


# ── Auth ──────────────────────────────────────────────────────────────────────


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str  # user_id as string
    exp: int | None = None


class StudentProfileCreate(BaseModel):
    notification_lesson: int | None = None
    notification_homework: int | None = None


class StudentProfilePublic(BaseModel):
    id: uuid.UUID
    student_id: uuid.UUID
    notification_lesson: int | None = None
    notification_homework: int | None = None

    model_config = ConfigDict(from_attributes=True)


class StudentProfileUpdate(BaseModel):
    notification_lesson: int | None = None
    notification_homework: int | None = None


class TeacherProfileCreate(BaseModel):
    code: str
    work_start: str | None = None  # "HH:MM" format
    work_end: str | None = None  # "HH:MM" format
    lesson_length: int = 60  # in minutes
    between_lessons_break: int = 0  # in minutes
    max_lessons_per_day: int = 6

    @field_validator("work_start", "work_end")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if v is None:
            return v
        try:
            hour, minute = map(int, v.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
        except Exception:
            raise ValueError(f"Invalid time format: {v}")
        return v


class TeacherProfilePublic(BaseModel):
    id: uuid.UUID
    teacher_id: uuid.UUID
    code: str
    work_start: time | None = None  # "HH:MM" format
    work_end: time | None = None  # "HH:MM" format
    lesson_length: int  # in minutes
    between_lessons_break: int = 0  # in minutes
    max_lessons_per_day: int = 6

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("work_start", "work_end")
    def serialize_time_fields(self, value: time | None) -> str | None:
        if value is None:
            return None
        return value.strftime("%H:%M")


class TeacherProfileUpdate(BaseModel):
    code: str | None = None
    work_start: str | None = None  # "HH:MM" format
    work_end: str | None = None  # "HH:MM" format
    lesson_length: int | None = None  # in minutes
    between_lessons_break: int = 0  # in minutes
    max_lessons_per_day: int = 6

    @field_validator("work_start", "work_end")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if v is None:
            return v
        try:
            hour, minute = map(int, v.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
        except Exception:
            raise ValueError(f"Invalid time format: {v}")
        return v
