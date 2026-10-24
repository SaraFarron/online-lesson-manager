from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, Self, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class BackendUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Backend unavailable. Please retry shortly; no offline login is possible."
        )


class BackendOutcomeUnknown(BackendUnavailable):
    def __init__(self) -> None:
        RuntimeError.__init__(
            self,
            "The save outcome is unknown. Reload the latest data before trying again; "
            "do not repeat the action.",
        )


class BackendError(RuntimeError):
    def __init__(
        self,
        status: int,
        detail: str,
        *,
        code: str | None = None,
        displacement_preview: BreakDisplacementPreview | None = None,
    ) -> None:
        self.status = status
        self.detail = detail
        self.code = code
        self.displacement_preview = displacement_preview
        super().__init__(detail)


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 3,
        recovery: float = 15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.recovery = recovery
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self.probing = False
        self.lock = asyncio.Lock()

    async def enter(self) -> bool:
        async with self.lock:
            if self.opened_at is None:
                return False
            if self.probing or self.clock() - self.opened_at < self.recovery:
                raise BackendUnavailable()
            self.probing = True
            return True

    async def finish(self, failed: bool, probe: bool) -> None:
        async with self.lock:
            if probe:
                self.probing = False
            if failed:
                self.failures += 1
                if probe or self.failures >= self.threshold:
                    self.opened_at = self.clock()
            elif probe or self.opened_at is None:
                self.failures = 0
                self.opened_at = None


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"]


class NoContent(BaseModel):
    model_config = ConfigDict(extra="forbid")


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

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid teacher timezone") from exc
        return value


ManagementResource = Literal[
    "lessons", "recurrent-lessons", "breaks", "recurrent-breaks", "vacations"
]


class EventManagementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"]
    start: AwareDatetime
    end: AwareDatetime
    user_id: uuid.UUID
    subject_label: str = Field(min_length=1)
    is_recurrent: bool
    cancelled_occurrences: int = Field(ge=0)
    visibility_status: (
        Literal["visible", "hidden_by_vacation", "not_applicable"] | None
    ) = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Managed event start must be before end")
        return self


class EventManagementPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EventManagementItem]
    total: int = Field(ge=0)


class RecurrentCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Cancellation start must be before end")
        return self


class ManagedOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime
    status: Literal["visible", "hidden_by_vacation", "cancelled"]

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Occurrence start must be before end")
        return self


class EventManagementDetail(EventManagementItem):
    interval: int | None = Field(default=None, gt=0, strict=True)
    interval_end: AwareDatetime | None = None
    recurrence_mode: Literal["UTC_STEP", "TEACHER_LOCAL"] | None = None
    recurrence_timezone: str | None = None
    cancels: list[RecurrentCancel] = Field(default_factory=list)
    revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    inspected_occurrence: ManagedOccurrence | None = None

    @model_validator(mode="after")
    def validate_recurrence_shape(self) -> Self:
        recurrence_fields = (self.interval, self.interval_end, self.recurrence_mode)
        if self.is_recurrent and any(value is None for value in recurrence_fields):
            raise ValueError("Recurring event details require recurrence metadata")
        if not self.is_recurrent and (
            any(value is not None for value in recurrence_fields)
            or self.recurrence_timezone is not None
            or self.cancels
        ):
            raise ValueError("One-off event details cannot contain recurrence metadata")
        return self


class EventWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    start: AwareDatetime
    end: AwareDatetime
    user_id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"]

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Event start must be before end")
        return self


class RecurrentEventWriteResult(EventWriteResult):
    interval: int = Field(gt=0, strict=True)
    interval_end: AwareDatetime


class EventCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime | None = None
    user_id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"]


class EventUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime
    user_id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"] | None = None


class RecurrentEventCreatePayload(EventCreatePayload):
    interval: int = Field(gt=0, strict=True)
    interval_end: AwareDatetime


class RecurrentEventUpdatePayload(EventUpdatePayload):
    interval: int = Field(gt=0, strict=True)
    interval_end: AwareDatetime
    cancels: list[RecurrentCancel] | None = None


class OccurrenceActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancelled_occurrence: RecurrentCancel
    revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    replacement: EventWriteResult | None = None


class RecurrentOccurrenceCancelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_start: AwareDatetime
    occurrence_end: AwareDatetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.occurrence_start >= self.occurrence_end:
            raise ValueError("Occurrence start must be before end")
        return self


class RecurrentOccurrenceMovePayload(RecurrentOccurrenceCancelPayload):
    replacement_start: AwareDatetime
    replacement_end: AwareDatetime

    @model_validator(mode="after")
    def validate_replacement(self) -> Self:
        if self.replacement_start >= self.replacement_end:
            raise ValueError("Replacement start must be before end")
        return self


class LocalIntervalResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def require_naive_local_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is not None:
            raise ValueError("Local datetimes must not include a timezone offset")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Local interval start must be before end")
        return self


class ResolvedLocalInstant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local: AwareDatetime
    utc: AwareDatetime
    utc_offset: str = Field(pattern=r"^[+-][0-9]{2}:[0-9]{2}$")

    @model_validator(mode="after")
    def validate_utc_offset(self) -> Self:
        if self.utc.utcoffset() != timedelta(0):
            raise ValueError("Resolved instant must be UTC")
        local_offset = self.local.strftime("%z")
        if self.utc_offset != f"{local_offset[:3]}:{local_offset[3:]}":
            raise ValueError("Local instant offset does not match its timezone offset")
        return self


class LocalIntervalResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str
    start: ResolvedLocalInstant
    end: ResolvedLocalInstant

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid resolution timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start.utc >= self.end.utc:
            raise ValueError("Resolved interval start must be before end")
        return self


class DisplacedOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["event", "recurrent_event"]
    source_id: uuid.UUID
    user_id: uuid.UUID
    event_type: Literal["LESSON"]
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Affected occurrence start must be before end")
        return self


class BreakDisplacementPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["break_displacement_confirmation_required"]
    message: Literal["Confirm cancellation of overlapping lesson occurrences"]
    confirmation_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    affected_occurrences: list[DisplacedOccurrence] = Field(min_length=1)


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


class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["event", "recurrent_event"]
    source_id: uuid.UUID
    event_type: Literal["LESSON", "BREAK", "VACATION"]
    start: AwareDatetime
    end: AwareDatetime
    status: Literal["past", "ongoing", "upcoming"]
    subject_label: str | None = None

    @model_validator(mode="after")
    def validate_interval_and_label(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Schedule item start must be before end")
        if self.subject_label is not None and (
            self.event_type != "LESSON" or not self.subject_label.strip()
        ):
            raise ValueError("Only named lessons may have a subject label")
        return self


class ScheduleDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    items: list[ScheduleItem]


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Literal["day", "week"]
    timezone: str
    generated_at: AwareDatetime
    range_start: AwareDatetime
    range_end: AwareDatetime
    days: list[ScheduleDay]

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid schedule timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_period_shape(self) -> Self:
        expected_days = 1 if self.period == "day" else 7
        if len(self.days) != expected_days:
            raise ValueError(
                f"{self.period} response must contain {expected_days} day(s)"
            )
        if any(
            later.date.toordinal() != earlier.date.toordinal() + 1
            for earlier, later in zip(self.days, self.days[1:], strict=False)
        ):
            raise ValueError("Schedule dates must be consecutive and ascending")
        if self.period == "week" and self.days[0].date.weekday() != 0:
            raise ValueError("Week response must start on Monday")
        if self.range_start >= self.range_end:
            raise ValueError("Schedule range start must be before end")
        return self


ErrorContext = Literal["default", "schedule", "scheduling"]
DEFAULT_ERROR_MESSAGES = {
    400: "Account inactive or request not allowed.",
    401: "Authentication failed. Check bot configuration or restart with /start.",
    403: "Access denied.",
    404: "Account or invitation not found.",
    409: "Account already registered.",
    422: "Invalid settings. Review the values before submitting again.",
}
SCHEDULE_ERROR_MESSAGES = {
    400: "This account cannot view a schedule.",
    401: "Schedule authentication failed. Please retry or restart with /start.",
    403: "This account does not have schedule access.",
    404: "The linked teacher is not available for schedule viewing.",
    409: "The linked teacher cannot provide a schedule.",
    422: "The schedule request or calendar data is invalid.",
}
SCHEDULING_ERROR_MESSAGES = {
    400: "This scheduling action is not allowed for this account.",
    401: "Authentication failed. Restart with /start and reload the event.",
    403: "This account does not have permission to manage this event.",
    404: "The selected student or event is no longer available.",
    409: "The request conflicts with the current schedule. Reload and try again.",
    422: "Check the date, time, and selected student.",
}
POLICY_ERROR_MESSAGES = {
    "Teacher work hours are not configured": "The teacher has not configured working hours.",
    "Teacher working days are not configured": "The teacher has not configured working days.",
    "Lesson starts on a non-working day": "That date is not one of the teacher's working days.",
    "Lesson starts before the teacher's work hours": "That time is before the teacher's working hours.",
    "Lesson ends after the teacher's work hours": "That time is after the teacher's working hours.",
    "Lesson cannot cross local midnight": "A lesson cannot cross local midnight in the teacher's timezone.",
    "Students must use the teacher's configured lesson duration": "Use the lesson duration configured by the teacher.",
    "Default lesson duration is outside the supported range": "The teacher's configured lesson duration is outside the supported range.",
    "Event overlaps an applicable vacation": "That time falls within an applicable vacation.",
    "Event overlaps an existing non-lesson event": "That time overlaps another scheduled event.",
    "Event overlaps an existing event": "That time overlaps another scheduled event.",
}


Model = TypeVar("Model", bound=BaseModel)


class BackendClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        credential: str,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.http = http
        self.credential = credential
        self.breaker = breaker or CircuitBreaker()
        self.tokens: dict[int, str] = {}
        self.pending_registrations: set[int] = set()

    async def request(
        self,
        method: str,
        path: str,
        model: type[Model],
        *,
        service: bool = False,
        token: str | None = None,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        error_context: ErrorContext = "default",
        _ambiguous_write: bool = False,
    ) -> Model:
        probe = await self.breaker.enter()
        failed = False
        request_headers = (
            {"X-Bot-Service-Key": self.credential}
            if service
            else {"Authorization": f"Bearer {token}"}
            if token
            else {}
        )
        if headers:
            request_headers.update(headers)
        try:
            try:
                response = await self.http.request(
                    method, path, headers=request_headers, params=params, json=body
                )
            except httpx.TransportError as exc:
                failed = True
                if _ambiguous_write:
                    raise BackendOutcomeUnknown() from exc
                raise BackendUnavailable() from exc
            if response.status_code >= 500:
                failed = True
                if _ambiguous_write:
                    raise BackendOutcomeUnknown()
                raise BackendUnavailable()
            if response.is_error:
                raise self._backend_error(response, error_context)
            if response.status_code == httpx.codes.NO_CONTENT:
                if model is NoContent:
                    return model()
                failed = True
                if _ambiguous_write:
                    raise BackendOutcomeUnknown()
                raise BackendUnavailable()
            if model is NoContent:
                failed = True
                if _ambiguous_write:
                    raise BackendOutcomeUnknown()
                raise BackendUnavailable()
            try:
                return model.model_validate_json(response.content)
            except ValidationError as exc:
                failed = True
                if _ambiguous_write:
                    raise BackendOutcomeUnknown() from exc
                raise BackendUnavailable() from exc
        except asyncio.CancelledError:
            failed = True
            raise
        finally:
            await self.breaker.finish(failed, probe)

    @staticmethod
    def _backend_error(
        response: httpx.Response, error_context: ErrorContext
    ) -> BackendError:
        try:
            response_body = response.json()
        except ValueError:
            response_body = None

        detail = (
            response_body.get("detail") if isinstance(response_body, dict) else None
        )
        code = detail.get("code") if isinstance(detail, dict) else None
        if code == "break_displacement_confirmation_required":
            try:
                preview = BreakDisplacementPreview.model_validate(detail)
            except ValidationError:
                pass
            else:
                return BackendError(
                    response.status_code,
                    "Review the affected lessons before confirming this break.",
                    code=preview.code,
                    displacement_preview=preview,
                )

        if code == "stale_event_revision":
            message = "This event changed. Reload it before editing."
        elif code == "occurrence_already_cancelled":
            message = "This lesson occurrence is already cancelled. Reload the series."
        elif (
            response.status_code == 409
            and detail == "Schedule is being changed; retry this request."
        ):
            message = "The schedule is being changed. Reload and try again."
        elif isinstance(detail, str) and detail in POLICY_ERROR_MESSAGES:
            message = POLICY_ERROR_MESSAGES[detail]
        elif isinstance(detail, str) and detail.startswith(
            "Teacher's daily lesson limit of "
        ):
            message = "The teacher's daily lesson limit would be exceeded."
        elif isinstance(detail, str) and detail.startswith(
            "Teacher's consecutive lesson limit of "
        ):
            message = "The teacher's consecutive lesson limit would be exceeded."
        else:
            messages = {
                "default": DEFAULT_ERROR_MESSAGES,
                "schedule": SCHEDULE_ERROR_MESSAGES,
                "scheduling": SCHEDULING_ERROR_MESSAGES,
            }[error_context]
            message = messages.get(
                response.status_code, "Backend rejected the request."
            )

        return BackendError(
            response.status_code,
            message,
            code=(
                code
                if isinstance(code, str)
                and code in {"stale_event_revision", "occurrence_already_cancelled"}
                else None
            ),
        )

    async def session(
        self,
        sender_id: int,
        chat_id: int,
        *,
        error_context: ErrorContext = "default",
    ) -> Token:
        try:
            token = await self.request(
                "POST",
                "/api/v1/telegram/session",
                Token,
                service=True,
                error_context=error_context,
                body={
                    "telegram_user_id": sender_id,
                    "telegram_private_chat_id": chat_id,
                },
            )
        except BackendError as exc:
            if exc.status in {400, 404}:
                self.tokens.pop(sender_id, None)
            raise
        self.tokens[sender_id] = token.access_token
        return token

    async def read(
        self,
        path: str,
        model: type[Model],
        sender_id: int,
        chat_id: int,
        *,
        params: dict[str, str] | None = None,
        error_context: ErrorContext = "default",
    ) -> Model:
        if sender_id not in self.tokens:
            await self.session(sender_id, chat_id, error_context=error_context)
        try:
            return await self.request(
                "GET",
                path,
                model,
                token=self.tokens[sender_id],
                params=params,
                error_context=error_context,
            )
        except BackendError as exc:
            if exc.status != 401:
                raise
            await self.session(sender_id, chat_id, error_context=error_context)
            return await self.request(
                "GET",
                path,
                model,
                token=self.tokens[sender_id],
                params=params,
                error_context=error_context,
            )

    async def resolve_local_interval(
        self,
        sender_id: int,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> LocalIntervalResolution:
        payload = LocalIntervalResolutionRequest(start=start, end=end)
        if sender_id not in self.tokens:
            await self.session(sender_id, chat_id, error_context="scheduling")
        try:
            return await self.request(
                "POST",
                "/api/v1/events/resolve-local-time",
                LocalIntervalResolution,
                token=self.tokens[sender_id],
                body=payload.model_dump(mode="json"),
                error_context="scheduling",
            )
        except BackendError as exc:
            if exc.status != 401:
                raise
            await self.session(sender_id, chat_id, error_context="scheduling")
            return await self.request(
                "POST",
                "/api/v1/events/resolve-local-time",
                LocalIntervalResolution,
                token=self.tokens[sender_id],
                body=payload.model_dump(mode="json"),
                error_context="scheduling",
            )

    async def write(
        self,
        method: Literal["POST", "PUT", "PATCH"],
        path: str,
        model: type[Model],
        sender_id: int,
        chat_id: int,
        payload: BaseModel,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        revision: str | None = None,
        confirm_displacement: bool = False,
        displacement_token: str | None = None,
        error_context: ErrorContext = "scheduling",
    ) -> Model:
        if sender_id not in self.tokens:
            await self.session(sender_id, chat_id, error_context=error_context)
        request_headers = dict(headers or {})
        if revision is not None:
            request_headers["If-Match"] = revision
        if displacement_token is not None:
            request_headers["X-Displacement-Token"] = displacement_token
        if confirm_displacement:
            params = {**(params or {}), "confirm_displacement": "true"}

        async def send() -> Model:
            return await self.request(
                method,
                path,
                model,
                token=self.tokens[sender_id],
                body=payload.model_dump(mode="json", exclude_unset=True),
                params=params,
                headers=request_headers,
                error_context=error_context,
                _ambiguous_write=True,
            )

        try:
            return await send()
        except BackendError as exc:
            if exc.status != 401:
                raise
            # A received 401 definitively rejects the write, so one renewed-token
            # attempt is safe. Transport failures and 5xx responses are never retried.
            await self.session(sender_id, chat_id, error_context=error_context)
            return await send()

    async def delete(
        self,
        path: str,
        sender_id: int,
        chat_id: int,
        *,
        revision: str | None = None,
        error_context: ErrorContext = "scheduling",
    ) -> None:
        if sender_id not in self.tokens:
            await self.session(sender_id, chat_id, error_context=error_context)
        request_headers = {"If-Match": revision} if revision is not None else None

        async def send() -> NoContent:
            return await self.request(
                "DELETE",
                path,
                NoContent,
                token=self.tokens[sender_id],
                headers=request_headers,
                error_context=error_context,
                _ambiguous_write=True,
            )

        try:
            await send()
        except BackendError as exc:
            if exc.status != 401:
                raise
            await self.session(sender_id, chat_id, error_context=error_context)
            await send()

    async def read_student_picker(
        self,
        sender_id: int,
        chat_id: int,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> StudentPickerPage:
        return await self.read(
            "/api/v1/auth/teacher/students",
            StudentPickerPage,
            sender_id,
            chat_id,
            params={"skip": str(skip), "limit": str(limit)},
            error_context="scheduling",
        )

    async def read_teacher_booking_context(
        self, sender_id: int, chat_id: int
    ) -> TeacherBookingContext:
        return await self.read(
            "/api/v1/auth/me/teacher-context",
            TeacherBookingContext,
            sender_id,
            chat_id,
            error_context="scheduling",
        )

    async def read_management_page(
        self,
        sender_id: int,
        chat_id: int,
        resource: ManagementResource,
        *,
        skip: int = 0,
        limit: int = 100,
        subject_id: uuid.UUID | None = None,
    ) -> EventManagementPage:
        params = {"skip": str(skip), "limit": str(limit)}
        if subject_id is not None:
            params["subject_id"] = str(subject_id)
        return await self.read(
            f"/api/v1/{resource}/",
            EventManagementPage,
            sender_id,
            chat_id,
            params=params,
            error_context="scheduling",
        )

    async def read_management_detail(
        self,
        sender_id: int,
        chat_id: int,
        resource: ManagementResource,
        event_id: uuid.UUID,
        *,
        occurrence_start: AwareDatetime | None = None,
    ) -> EventManagementDetail:
        params = (
            {"occurrence_start": occurrence_start.isoformat()}
            if occurrence_start is not None
            else None
        )
        return await self.read(
            f"/api/v1/{resource}/{event_id}",
            EventManagementDetail,
            sender_id,
            chat_id,
            params=params,
            error_context="scheduling",
        )

    async def write_management_event(
        self,
        method: Literal["POST", "PUT"],
        resource: ManagementResource,
        sender_id: int,
        chat_id: int,
        payload: BaseModel,
        model: type[Model],
        *,
        event_id: uuid.UUID | None = None,
        revision: str | None = None,
        confirm_displacement: bool = False,
        displacement_token: str | None = None,
    ) -> Model:
        if method == "PUT" and event_id is None:
            raise ValueError("Updating a managed event requires its ID")
        if method == "POST" and (event_id is not None or revision is not None):
            raise ValueError(
                "Creating a managed event cannot include an ID or revision"
            )
        path = f"/api/v1/{resource}/"
        if event_id is not None:
            path += str(event_id)
        return await self.write(
            method,
            path,
            model,
            sender_id,
            chat_id,
            payload,
            revision=revision,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
        )

    async def delete_management_event(
        self,
        resource: ManagementResource,
        event_id: uuid.UUID,
        sender_id: int,
        chat_id: int,
        *,
        revision: str,
    ) -> None:
        await self.delete(
            f"/api/v1/{resource}/{event_id}",
            sender_id,
            chat_id,
            revision=revision,
        )

    async def cancel_recurrent_occurrence(
        self,
        sender_id: int,
        chat_id: int,
        lesson_id: uuid.UUID,
        payload: RecurrentOccurrenceCancelPayload,
    ) -> OccurrenceActionResult:
        return await self.write(
            "POST",
            f"/api/v1/recurrent-lessons/{lesson_id}/occurrences/cancel",
            OccurrenceActionResult,
            sender_id,
            chat_id,
            payload,
        )

    async def move_recurrent_occurrence(
        self,
        sender_id: int,
        chat_id: int,
        lesson_id: uuid.UUID,
        payload: RecurrentOccurrenceMovePayload,
    ) -> OccurrenceActionResult:
        return await self.write(
            "POST",
            f"/api/v1/recurrent-lessons/{lesson_id}/occurrences/move",
            OccurrenceActionResult,
            sender_id,
            chat_id,
            payload,
        )

    async def update_me(
        self, sender_id: int, chat_id: int, payload: SelfProfileUpdate
    ) -> Account:
        return await self.write(
            "PATCH",
            "/api/v1/auth/me",
            Account,
            sender_id,
            chat_id,
            payload,
        )

    async def update_teacher_profile(
        self,
        sender_id: int,
        chat_id: int,
        user_id: uuid.UUID,
        payload: TeacherProfileUpdate,
    ) -> TeacherProfile:
        return await self.write(
            "PUT",
            f"/api/v1/auth/users/{user_id}/teacher-profile",
            TeacherProfile,
            sender_id,
            chat_id,
            payload,
        )

    async def update_student_profile(
        self,
        sender_id: int,
        chat_id: int,
        user_id: uuid.UUID,
        payload: StudentProfileUpdate,
    ) -> StudentProfile:
        return await self.write(
            "PUT",
            f"/api/v1/auth/users/{user_id}/student-profile",
            StudentProfile,
            sender_id,
            chat_id,
            payload,
        )

    async def read_schedule_day(
        self,
        sender_id: int,
        chat_id: int,
        requested_date: date | None = None,
    ) -> ScheduleResponse:
        params = {"date": requested_date.isoformat()} if requested_date else None
        result = await self.read(
            "/api/v1/schedule/day",
            ScheduleResponse,
            sender_id,
            chat_id,
            params=params,
            error_context="schedule",
        )
        if result.period != "day":
            raise BackendUnavailable()
        return result

    async def read_schedule_week(
        self,
        sender_id: int,
        chat_id: int,
        requested_date: date | None = None,
    ) -> ScheduleResponse:
        params = {"date": requested_date.isoformat()} if requested_date else None
        result = await self.read(
            "/api/v1/schedule/week",
            ScheduleResponse,
            sender_id,
            chat_id,
            params=params,
            error_context="schedule",
        )
        if result.period != "week":
            raise BackendUnavailable()
        return result

    async def view(self, sender_id: int, chat_id: int) -> SessionView:
        account = await self.read("/api/v1/auth/me", Account, sender_id, chat_id)
        view = SessionView(account=account)
        if account.role == "TEACHER":
            view.teacher = await self.read(
                f"/api/v1/auth/users/{account.id}/teacher-profile",
                TeacherProfile,
                sender_id,
                chat_id,
            )
        elif account.role == "STUDENT":
            view.student = await self.read(
                f"/api/v1/auth/users/{account.id}/student-profile",
                StudentProfile,
                sender_id,
                chat_id,
            )
        return view

    async def login(self, sender_id: int, chat_id: int) -> SessionView | None:
        try:
            await self.session(sender_id, chat_id)
        except BackendError as exc:
            if exc.status == 404:
                return None
            raise
        return await self.view(sender_id, chat_id)

    async def resolve(self, code: str) -> TeacherConfirmation:
        return await self.request(
            "POST",
            "/api/v1/telegram/teacher-code/resolve",
            TeacherConfirmation,
            service=True,
            body={"teacher_code": code},
        )

    async def register(self, data: Registration) -> SessionView:
        sender_id = data.telegram_user_id
        if sender_id in self.pending_registrations:
            # An ambiguous previous write must be resolved before another write.
            existing = await self.login(sender_id, data.telegram_private_chat_id)
            if existing is not None:
                self.pending_registrations.discard(sender_id)
                return existing
            self.pending_registrations.discard(sender_id)
        try:
            token = await self.request(
                "POST",
                "/api/v1/telegram/register",
                Token,
                service=True,
                body=data.model_dump(
                    mode="json",
                    exclude={"teacher_code"} if data.role == "TEACHER" else set(),
                ),
            )
        except BackendUnavailable:
            self.pending_registrations.add(sender_id)
            existing = await self.login(sender_id, data.telegram_private_chat_id)
            if existing is None:
                raise BackendUnavailable()
            self.pending_registrations.discard(sender_id)
            return existing
        except BackendError as exc:
            if exc.status != 409:
                raise
            existing = await self.login(sender_id, data.telegram_private_chat_id)
            if existing is None:
                raise
            return existing
        self.tokens[sender_id] = token.access_token
        # A committed registration with a failed subsequent read is also pending.
        self.pending_registrations.add(sender_id)
        view = await self.view(sender_id, data.telegram_private_chat_id)
        self.pending_registrations.discard(sender_id)
        return view
