import uuid
from datetime import datetime, time
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.activity.schemas import StudentActionHistoryItem
from backend.auth.constants import Roles
from backend.auth.utils import string_to_time
from backend.events.time_utils import normalize_utc


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Invalid timezone: {value}") from exc
    return value


def validate_work_time(value: str) -> str:
    string_to_time(value)
    return value


def validate_working_days(value: list[int]) -> list[int]:
    if len(value) != len(set(value)):
        raise ValueError("Working days must be unique")
    return value


Timezone = Annotated[str, AfterValidator(validate_timezone)]
DigestMinute = Annotated[int, Field(ge=0, le=1439, strict=True)]
LessonReminder = Annotated[int, Field(ge=1, le=600, strict=True)]
HomeworkReminder = Annotated[int, Field(ge=1, strict=True)]
PositiveInt = Annotated[int, Field(ge=1, strict=True)]
BreakMinutes = Annotated[int, Field(ge=0, strict=True)]
WorkTime = Annotated[
    str, Field(pattern=r"^[0-9]{2}:[0-9]{2}$"), AfterValidator(validate_work_time)
]
WorkingDays = Annotated[
    list[Annotated[int, Field(ge=1, le=7, strict=True)]],
    Field(min_length=1, max_length=7),
    AfterValidator(validate_working_days),
]
TeacherCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,56}$")]

# ── User ──────────────────────────────────────────────────────────────────────


class UserBase(BaseModel):
    full_name: str | None = Field(default=None, max_length=256)
    is_active: bool = True
    is_superuser: bool = False


class UserCreate(UserBase):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Roles
    timezone: Timezone


class UserRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=256)
    role: Literal[Roles.TEACHER, Roles.STUDENT]
    timezone: Timezone


class UserUpdate(BaseModel):
    """Admin: update any user field."""

    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
    is_superuser: bool | None = None


class UserUpdateMe(BaseModel):
    """Self-service profile update — cannot escalate privileges."""

    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, max_length=256)
    email: EmailStr | None = None
    timezone: Timezone | None = None
    notification_lessons_today: DigestMinute | None = None

    @field_validator("email", "timezone", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Email and timezone cannot be explicitly null")
        return value


class UserPublic(UserBase):
    email: EmailStr | None
    id: uuid.UUID
    timezone: str
    role: Roles
    teacher_id: uuid.UUID | None
    notification_lessons_today: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserList(BaseModel):
    items: list[UserPublic]
    total: int


class TeacherBookingContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    teacher_id: uuid.UUID
    timezone: str
    work_start: str | None
    work_end: str | None
    working_days: list[int]
    lesson_length: int
    between_lessons_break: int = Field(ge=0)


class StudentPickerItem(BaseModel):
    id: uuid.UUID
    display_label: str


class StudentPickerPage(BaseModel):
    items: list[StudentPickerItem]
    total: int


class StudentHomeworkSummary(BaseModel):
    assigned: int
    completed: int
    incomplete: int
    completed_by_student: int
    completed_by_teacher: int


class TeacherStudentProfileSummary(BaseModel):
    relationship_id: uuid.UUID
    student_id: uuid.UUID
    display_name: str = Field(
        description="Uses 'Name unavailable' when the student has no display name."
    )
    username_display: str = Field(
        description="Uses 'No username' when no Telegram username is recorded."
    )
    linked_at: datetime | None = Field(
        description="Null means the historical association date is unknown."
    )
    removed_at: datetime | None
    homework: StudentHomeworkSummary

    @field_serializer("linked_at", "removed_at", when_used="json-unless-none")
    def serialize_relationship_datetimes(self, value: datetime) -> str:
        return normalize_utc(value).isoformat()


class TeacherStudentProfileDetail(TeacherStudentProfileSummary):
    actions: list[StudentActionHistoryItem]


class TeacherStudentProfilePage(BaseModel):
    items: list[TeacherStudentProfileSummary]
    total: int


# ── Auth ──────────────────────────────────────────────────────────────────────


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str  # user_id as string
    exp: int | None = None


class StudentJoinTeacher(BaseModel):
    teacher_code: TeacherCode


class StudentRemovalConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str = Field(min_length=1, max_length=1024)
    operation_key: uuid.UUID


class StudentRemovalImpact(BaseModel):
    one_off_lessons_to_delete: int
    recurring_series_to_truncate: int
    recurring_series_to_delete: int
    future_cancellations_to_delete: int
    staged_uploads_to_revoke: int
    notifications_to_suppress: int


class StudentRemovalPreview(BaseModel):
    student_id: uuid.UUID
    relationship_id: uuid.UUID
    impact: StudentRemovalImpact
    confirmation_token: str
    expires_at: datetime


class StudentRemovalResult(BaseModel):
    student_id: uuid.UUID
    relationship_id: uuid.UUID
    operation_key: uuid.UUID
    removed_at: datetime
    impact: StudentRemovalImpact
    cleanup_complete: bool = True


class StudentProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notification_lesson: LessonReminder | None = 15
    notification_homework: HomeworkReminder | None = 2
    notification_homework_assignment: bool = True


class StudentProfilePublic(BaseModel):
    id: uuid.UUID
    student_id: uuid.UUID
    notification_lesson: int | None = None
    notification_homework: int | None = None
    notification_homework_assignment: bool = True

    model_config = ConfigDict(from_attributes=True)


class StudentProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notification_lesson: LessonReminder | None = None
    notification_homework: HomeworkReminder | None = None
    notification_homework_assignment: bool | None = None

    @field_validator("notification_homework_assignment", mode="before")
    @classmethod
    def reject_null_assignment_toggle(cls, value: object) -> object:
        if value is None:
            raise ValueError("Assignment notification toggle cannot be null")
        return value


class TeacherPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_start: WorkTime = "09:00"
    work_end: WorkTime = "18:00"
    working_days: WorkingDays = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    lesson_length: PositiveInt = 60
    between_lessons_break: BreakMinutes = 5
    max_consecutive_lessons: PositiveInt = 4
    max_lessons_per_day: PositiveInt = 6
    notification_homework_submission: bool = True

    @model_validator(mode="after")
    def validate_preferences(self) -> Self:
        if string_to_time(self.work_end) <= string_to_time(self.work_start):
            raise ValueError("Work end must be later than work start")
        if self.max_consecutive_lessons > self.max_lessons_per_day:
            raise ValueError("Consecutive lesson limit cannot exceed daily limit")
        return self


class TeacherProfileCreate(TeacherPreferences):
    code: TeacherCode | None = None


class TeacherProfilePublic(BaseModel):
    id: uuid.UUID
    teacher_id: uuid.UUID
    code: str
    work_start: time | None = None  # "HH:MM" format
    work_end: time | None = None  # "HH:MM" format
    lesson_length: int  # in minutes
    working_days: list[int]
    max_consecutive_lessons: int
    between_lessons_break: int = Field(
        default=5,
        description=(
            "Minutes reserved after newly created lessons; existing lessons and "
            "series keep their saved value."
        ),
    )
    max_lessons_per_day: int = 6
    notification_homework_submission: bool = True

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("work_start", "work_end")
    def serialize_time_fields(self, value: time | None) -> str | None:
        if value is None:
            return None
        return value.strftime("%H:%M")


class TeacherProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: TeacherCode | None = None
    work_start: WorkTime | None = None
    work_end: WorkTime | None = None
    working_days: WorkingDays | None = None
    lesson_length: PositiveInt | None = None
    between_lessons_break: BreakMinutes | None = Field(
        default=None,
        description=(
            "Applies only to new lessons and series; existing items keep their "
            "saved post-break duration."
        ),
    )
    max_consecutive_lessons: PositiveInt | None = None
    max_lessons_per_day: PositiveInt | None = None
    notification_homework_submission: bool | None = None

    @field_validator(
        "code",
        "work_start",
        "work_end",
        "working_days",
        "lesson_length",
        "between_lessons_break",
        "max_consecutive_lessons",
        "max_lessons_per_day",
        "notification_homework_submission",
        mode="before",
    )
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Required teacher preferences cannot be null")
        return value
