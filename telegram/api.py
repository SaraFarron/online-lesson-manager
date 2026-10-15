import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError


class BackendUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Backend unavailable. Please retry shortly; no offline login is possible."
        )


class BackendError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
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
    ) -> Model:
        probe = await self.breaker.enter()
        failed = False
        headers = (
            {"X-Bot-Service-Key": self.credential}
            if service
            else {"Authorization": f"Bearer {token}"}
            if token
            else {}
        )
        try:
            try:
                response = await self.http.request(
                    method, path, headers=headers, json=body
                )
            except httpx.TransportError as exc:
                failed = True
                raise BackendUnavailable() from exc
            if response.status_code >= 500:
                failed = True
                raise BackendUnavailable()
            if response.is_error:
                # Do not display arbitrary backend payloads or echo rejected input.
                messages = {
                    400: "Account inactive or request not allowed.",
                    401: "Authentication failed. Check bot configuration or restart with /start.",
                    403: "Access denied.",
                    404: "Account or invitation not found.",
                    409: "Account already registered.",
                    422: "Invalid settings. Review the values before submitting again.",
                }
                raise BackendError(
                    response.status_code,
                    messages.get(response.status_code, "Backend rejected the request."),
                )
            try:
                return model.model_validate_json(response.content)
            except ValidationError as exc:
                failed = True
                raise BackendUnavailable() from exc
        except asyncio.CancelledError:
            failed = True
            raise
        finally:
            await self.breaker.finish(failed, probe)

    async def session(self, sender_id: int, chat_id: int) -> Token:
        try:
            token = await self.request(
                "POST",
                "/api/v1/telegram/session",
                Token,
                service=True,
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
        self, path: str, model: type[Model], sender_id: int, chat_id: int
    ) -> Model:
        if sender_id not in self.tokens:
            await self.session(sender_id, chat_id)
        try:
            return await self.request("GET", path, model, token=self.tokens[sender_id])
        except BackendError as exc:
            if exc.status != 401:
                raise
            await self.session(sender_id, chat_id)
            return await self.request("GET", path, model, token=self.tokens[sender_id])

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
