from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class Account(BaseModel):
    id: uuid.UUID
    full_name: str | None
    role: Literal["TEACHER", "STUDENT", "ADMIN"]
    timezone: str
    teacher_id: uuid.UUID | None
    notification_lessons_today: int | None


class TeacherProfile(BaseModel):
    code: str
    work_start: str | None
    work_end: str | None
    working_days: list[int]
    lesson_length: int
    between_lessons_break: int
    max_consecutive_lessons: int
    max_lessons_per_day: int


class StudentProfile(BaseModel):
    notification_lesson: int | None
    notification_homework: int | None


class StudentPickerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    display_label: str


class StudentPickerPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StudentPickerItem]
    total: int = Field(ge=0)


class TeacherBookingContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teacher_id: uuid.UUID
    timezone: str
    work_start: str | None
    work_end: str | None
    working_days: list[int]
    lesson_length: int = Field(gt=0)
    between_lessons_break: int = Field(ge=0)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid teacher timezone") from exc
        return value


class TeacherConfirmation(BaseModel):
    teacher_id: uuid.UUID
    full_name: str | None


class Registration(BaseModel):
    telegram_user_id: int
    telegram_private_chat_id: int
    full_name: str | None
    role: Literal["TEACHER", "STUDENT"]
    timezone: str
    notification_lessons_today: int | None = 540
    profile: dict[str, Any] = Field(default_factory=dict)
    teacher_code: str | None = None


class SessionView(BaseModel):
    account: Account
    teacher: TeacherProfile | None = None
    student: StudentProfile | None = None


class SelfProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=256)
    email: EmailStr | None = None
    timezone: str | None = None
    notification_lessons_today: int | None = Field(
        default=None, ge=0, le=1439, strict=True
    )

    @field_validator("email", "timezone", mode="before")
    @classmethod
    def reject_explicit_null_credentials(cls, value: object) -> object:
        if value is None:
            raise ValueError("Email and timezone cannot be null")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid timezone") from exc
        return value


class TeacherProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,56}$")
    work_start: str | None = Field(default=None, pattern=r"^[0-9]{2}:[0-9]{2}$")
    work_end: str | None = Field(default=None, pattern=r"^[0-9]{2}:[0-9]{2}$")
    working_days: list[Annotated[int, Field(ge=1, le=7, strict=True)]] | None = Field(
        default=None, min_length=1, max_length=7
    )
    lesson_length: int | None = Field(default=None, gt=0, strict=True)
    between_lessons_break: int | None = Field(default=None, ge=0, strict=True)
    max_consecutive_lessons: int | None = Field(default=None, gt=0, strict=True)
    max_lessons_per_day: int | None = Field(default=None, gt=0, strict=True)

    @field_validator(
        "code",
        "work_start",
        "work_end",
        "working_days",
        "lesson_length",
        "between_lessons_break",
        "max_consecutive_lessons",
        "max_lessons_per_day",
        mode="before",
    )
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Teacher preferences cannot be null")
        return value

    @model_validator(mode="after")
    def validate_work_days_and_times(self) -> Self:
        if self.working_days is not None and (
            any(type(day) is not int or not 1 <= day <= 7 for day in self.working_days)
            or len(set(self.working_days)) != len(self.working_days)
        ):
            raise ValueError("Working days must be unique ISO weekdays")
        for value in (self.work_start, self.work_end):
            if value is not None:
                hour, minute = (int(part) for part in value.split(":"))
                if hour > 23 or minute > 59:
                    raise ValueError("Work time is invalid")
        if (
            self.work_start is not None
            and self.work_end is not None
            and self.work_end <= self.work_start
        ):
            raise ValueError("Work end must be later than work start")
        if (
            self.max_consecutive_lessons is not None
            and self.max_lessons_per_day is not None
            and self.max_consecutive_lessons > self.max_lessons_per_day
        ):
            raise ValueError("Consecutive lesson limit cannot exceed daily limit")
        return self


class StudentProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_lesson: int | None = Field(default=None, ge=1, le=600, strict=True)
    notification_homework: int | None = Field(default=None, ge=1, strict=True)
