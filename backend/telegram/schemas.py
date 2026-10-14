import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.auth.constants import Roles
from backend.auth.schemas import (
    DigestMinute,
    StudentProfileCreate,
    TeacherCode,
    TeacherPreferences,
    Timezone,
)

TelegramID = Annotated[int, Field(gt=0, le=2**63 - 1, strict=True)]


class TelegramSender(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telegram_user_id: TelegramID
    telegram_private_chat_id: TelegramID | None = None


class ErrorResponse(BaseModel):
    detail: str


class TelegramRegistrationBase(TelegramSender):
    timezone: Timezone
    full_name: str | None = Field(default=None, max_length=256)
    notification_lessons_today: DigestMinute | None = 540


class TeacherRegistration(TelegramRegistrationBase):
    role: Literal[Roles.TEACHER]
    profile: TeacherPreferences = Field(default_factory=TeacherPreferences)


class StudentRegistration(TelegramRegistrationBase):
    role: Literal[Roles.STUDENT]
    teacher_code: TeacherCode
    profile: StudentProfileCreate = Field(default_factory=StudentProfileCreate)


TelegramRegistration = Annotated[
    TeacherRegistration | StudentRegistration, Field(discriminator="role")
]


class TeacherCodeResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teacher_code: TeacherCode


class TeacherConfirmation(BaseModel):
    teacher_id: uuid.UUID
    full_name: str | None
