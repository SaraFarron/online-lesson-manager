from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Callable
from datetime import date, datetime
from typing import Any, Literal, TypeVar
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, BaseModel, ValidationError

from . import transfer
from .circuit_breaker import CircuitBreaker
from .errors import (
    BackendContractError,
    BackendError,
    BackendOutcomeUnknown,
    BackendUnavailable,
    BroadcastOutcomeUnknown,
    BroadcastPreviewStale,
    HomeworkAttachmentTooLarge,
    HomeworkOutcomeUnknown,
    StudentRemovalOutcomeUnknown,
)
from .messages import (
    BROADCAST_ERROR_MESSAGES,
    DEFAULT_ERROR_MESSAGES,
    HOMEWORK_ERROR_MESSAGES,
    POLICY_ERROR_MESSAGES,
    SCHEDULE_ERROR_MESSAGES,
    SCHEDULING_ERROR_MESSAGES,
    SETTINGS_ERROR_MESSAGES,
    ErrorContext,
)
from .models import (
    Account,
    BreakDisplacementPreview,
    EventManagementDetail,
    EventManagementPage,
    HomeworkAssignmentCreated,
    HomeworkAssignmentDetail,
    HomeworkAssignmentPage,
    HomeworkAssignmentPayload,
    HomeworkCompletionResult,
    HomeworkSubmissionPage,
    HomeworkSubmissionPayload,
    HomeworkSubmissionResult,
    LocalIntervalResolution,
    LocalIntervalResolutionRequest,
    ManagementResource,
    NoContent,
    OccurrenceActionResult,
    RecurrentOccurrenceCancelPayload,
    RecurrentOccurrenceMovePayload,
    Registration,
    ScheduleResponse,
    SelfProfileUpdate,
    SessionView,
    StagedHomeworkAttachment,
    StudentPickerPage,
    StudentProfile,
    StudentProfileUpdate,
    StudentRemovalConfirmRequest,
    StudentRemovalPreview,
    StudentRemovalResult,
    TeacherBookingContext,
    TeacherBroadcastPreview,
    TeacherBroadcastPreviewRequest,
    TeacherBroadcastPublication,
    TeacherBroadcastPublishRequest,
    TeacherBroadcastResults,
    TeacherConfirmation,
    TeacherProfile,
    TeacherProfileUpdate,
    TeacherStudentProfileDetail,
    TeacherStudentProfilePage,
    Token,
)
from .schedule_cache import (
    ScheduleCacheIdentity,
    SchedulePeriod,
    ScheduleReadResult,
    ScheduleSnapshotCache,
)

Model = TypeVar("Model", bound=BaseModel)


class BackendClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        credential: str,
        breaker: CircuitBreaker | None = None,
        schedule_cache: ScheduleSnapshotCache | None = None,
    ) -> None:
        self.http = http
        self.credential = credential
        self.breaker = breaker or CircuitBreaker()
        self.schedule_cache = (
            schedule_cache if schedule_cache is not None else ScheduleSnapshotCache()
        )
        self.tokens: dict[int, str] = {}
        self.telegram_usernames: dict[int, str | None] = {}
        self.pending_registrations: set[int] = set()
        self.pending_broadcast_operations: dict[
            int, tuple[uuid.UUID, TeacherBroadcastPublishRequest]
        ] = {}

    @property
    def _backend_namespace(self) -> str:
        base_url = self.http.base_url
        host = f"[{base_url.host}]" if ":" in base_url.host else base_url.host
        port = f":{base_url.port}" if base_url.port is not None else ""
        return f"{base_url.scheme}://{host}{port}{base_url.path.rstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        model: type[Model],
        *,
        service: bool = False,
        token: str | None = None,
        body: dict[str, Any] | None = None,
        content: AsyncIterable[bytes] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
        error_context: ErrorContext = "default",
        _strict_response: bool = False,
        _ambiguous_write: bool = False,
    ) -> Model:
        if body is not None and content is not None:
            raise ValueError("A request cannot include both JSON and stream content")
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
                request_options: dict[str, Any] = {
                    "headers": request_headers,
                    "params": params,
                }
                if timeout is not None:
                    request_options["timeout"] = timeout
                if content is not None:
                    request_options["content"] = content
                else:
                    request_options["json"] = body
                response = await self.http.request(method, path, **request_options)
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
                if _ambiguous_write:
                    failed = True
                    raise BackendOutcomeUnknown()
                if _strict_response:
                    raise BackendContractError()
                failed = True
                raise BackendUnavailable()
            if model is NoContent:
                if _ambiguous_write:
                    failed = True
                    raise BackendOutcomeUnknown()
                if _strict_response:
                    raise BackendContractError()
                failed = True
                raise BackendUnavailable()
            try:
                return model.model_validate_json(response.content)
            except ValidationError as exc:
                if _ambiguous_write:
                    failed = True
                    raise BackendOutcomeUnknown() from exc
                if _strict_response:
                    raise BackendContractError() from exc
                failed = True
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

        if response.status_code == 409 and detail == "broadcast_preview_stale":
            return BroadcastPreviewStale()
        if code == "stale_event_revision":
            message = "This event changed. Reload it before editing."
        elif code == "occurrence_already_cancelled":
            message = "This lesson occurrence is already cancelled. Reload the series."
        elif response.status_code == 422 and detail == "Break not found":
            message = "This break is no longer available. Refresh the list."
        elif response.status_code == 422 and detail == "Vacation not found":
            message = "This vacation is no longer available. Refresh the list."
        elif response.status_code == 422 and detail in {
            "Lesson not found",
            "Recurring event not found",
            "One-off event not found",
        }:
            message = "This lesson is no longer available. Refresh the list."
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
                "settings": SETTINGS_ERROR_MESSAGES,
                "homework": HOMEWORK_ERROR_MESSAGES,
                "broadcast": BROADCAST_ERROR_MESSAGES,
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
                and code
                in {
                    "stale_event_revision",
                    "occurrence_already_cancelled",
                    "broadcast_preview_stale",
                }
                else None
            ),
        )

    async def session(
        self,
        sender_id: int,
        chat_id: int,
        *,
        telegram_username: str | None = None,
        sync_username: bool = False,
        error_context: ErrorContext = "default",
        strict_response: bool = False,
    ) -> Token:
        if sync_username:
            self.telegram_usernames[sender_id] = telegram_username
        body: dict[str, int | str | None] = {
            "telegram_user_id": sender_id,
            "telegram_private_chat_id": chat_id,
        }
        if sender_id in self.telegram_usernames:
            body["telegram_username"] = self.telegram_usernames[sender_id]
        try:
            token = await self.request(
                "POST",
                "/api/v1/telegram/session",
                Token,
                service=True,
                error_context=error_context,
                _strict_response=strict_response,
                body=body,
            )
        except BackendError as exc:
            if exc.status in {400, 404}:
                self.tokens.pop(sender_id, None)
                self.schedule_cache.invalidate(self._backend_namespace, sender_id)
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
        strict_response: bool = False,
    ) -> Model:
        if sender_id not in self.tokens:
            await self.session(
                sender_id,
                chat_id,
                error_context=error_context,
                strict_response=strict_response,
            )
        try:
            return await self.request(
                "GET",
                path,
                model,
                token=self.tokens[sender_id],
                params=params,
                error_context=error_context,
                _strict_response=strict_response,
            )
        except BackendError as exc:
            if exc.status != 401:
                raise
            await self.session(
                sender_id,
                chat_id,
                error_context=error_context,
                strict_response=strict_response,
            )
            return await self.request(
                "GET",
                path,
                model,
                token=self.tokens[sender_id],
                params=params,
                error_context=error_context,
                _strict_response=strict_response,
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
        ambiguous_write: bool = True,
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
                _ambiguous_write=ambiguous_write,
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

    async def read_homework_assignments(
        self,
        sender_id: int,
        chat_id: int,
        *,
        skip: int = 0,
        limit: int = 100,
        status: Literal["INCOMPLETE", "COMPLETE"] | None = None,
        student_id: uuid.UUID | None = None,
    ) -> HomeworkAssignmentPage:
        params = {"skip": str(skip), "limit": str(limit)}
        if status is not None:
            params["status"] = status
        if student_id is not None:
            params["student_id"] = str(student_id)
        return await self.read(
            "/api/v1/homework/assignments",
            HomeworkAssignmentPage,
            sender_id,
            chat_id,
            params=params,
            error_context="homework",
        )

    async def preview_teacher_broadcast(
        self,
        sender_id: int,
        chat_id: int,
        text: str,
    ) -> TeacherBroadcastPreview:
        return await self.write(
            "POST",
            "/api/v1/notifications/broadcasts/preview",
            TeacherBroadcastPreview,
            sender_id,
            chat_id,
            TeacherBroadcastPreviewRequest(text=text),
            error_context="broadcast",
            ambiguous_write=False,
        )

    async def publish_teacher_broadcast(
        self,
        sender_id: int,
        chat_id: int,
        request: TeacherBroadcastPublishRequest,
        operation_key: uuid.UUID,
    ) -> TeacherBroadcastPublication:
        pending = self.pending_broadcast_operations.get(sender_id)
        if pending is not None:
            pending_key, pending_request = pending
            if pending_key != operation_key or pending_request != request:
                raise BroadcastOutcomeUnknown(pending_key, pending_request)
            try:
                publication = await self.reconcile_teacher_broadcast(
                    sender_id, chat_id, pending_key
                )
            except BackendError as exc:
                if exc.status != 404:
                    raise BroadcastOutcomeUnknown(pending_key, pending_request) from exc
            except BackendUnavailable as exc:
                raise BroadcastOutcomeUnknown(pending_key, pending_request) from exc
            else:
                return publication

        self.pending_broadcast_operations[sender_id] = (operation_key, request)
        try:
            publication = await self.write(
                "POST",
                "/api/v1/notifications/broadcasts",
                TeacherBroadcastPublication,
                sender_id,
                chat_id,
                request,
                headers={"Idempotency-Key": str(operation_key)},
                error_context="broadcast",
            )
        except BackendOutcomeUnknown as exc:
            raise BroadcastOutcomeUnknown(operation_key, request) from exc
        except BackendError:
            self.pending_broadcast_operations.pop(sender_id, None)
            raise
        except BackendUnavailable as exc:
            raise BroadcastOutcomeUnknown(operation_key, request) from exc
        except asyncio.CancelledError:
            raise
        else:
            self.pending_broadcast_operations.pop(sender_id, None)
            return publication

    async def reconcile_teacher_broadcast(
        self,
        sender_id: int,
        chat_id: int,
        operation_key: uuid.UUID,
    ) -> TeacherBroadcastPublication:
        publication = await self.read(
            f"/api/v1/notifications/broadcasts/operations/{operation_key}",
            TeacherBroadcastPublication,
            sender_id,
            chat_id,
            error_context="broadcast",
        )
        pending = self.pending_broadcast_operations.get(sender_id)
        if pending is not None and pending[0] == operation_key:
            self.pending_broadcast_operations.pop(sender_id, None)
        return publication

    async def read_teacher_broadcast_results(
        self,
        sender_id: int,
        chat_id: int,
        operation_key: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> TeacherBroadcastResults:
        return await self.read(
            f"/api/v1/notifications/broadcasts/operations/{operation_key}/results",
            TeacherBroadcastResults,
            sender_id,
            chat_id,
            params={"skip": str(skip), "limit": str(limit)},
            error_context="broadcast",
        )

    async def read_homework_assignment(
        self,
        sender_id: int,
        chat_id: int,
        homework_id: uuid.UUID,
    ) -> HomeworkAssignmentDetail:
        return await self.read(
            f"/api/v1/homework/assignments/{homework_id}",
            HomeworkAssignmentDetail,
            sender_id,
            chat_id,
            error_context="homework",
        )

    async def create_homework_assignment(
        self,
        sender_id: int,
        chat_id: int,
        payload: HomeworkAssignmentPayload,
        operation_key: uuid.UUID,
    ) -> HomeworkAssignmentCreated:
        try:
            return await self.write(
                "POST",
                "/api/v1/homework/assignments",
                HomeworkAssignmentCreated,
                sender_id,
                chat_id,
                payload,
                headers={"Idempotency-Key": str(operation_key)},
                error_context="homework",
            )
        except BackendOutcomeUnknown as exc:
            raise HomeworkOutcomeUnknown() from exc

    async def submit_homework(
        self,
        sender_id: int,
        chat_id: int,
        homework_id: uuid.UUID,
        payload: HomeworkSubmissionPayload,
    ) -> HomeworkSubmissionResult:
        try:
            return await self.write(
                "POST",
                f"/api/v1/homework/assignments/{homework_id}/submissions",
                HomeworkSubmissionResult,
                sender_id,
                chat_id,
                payload,
                error_context="homework",
            )
        except BackendOutcomeUnknown as exc:
            raise HomeworkOutcomeUnknown() from exc

    async def read_homework_submissions(
        self,
        sender_id: int,
        chat_id: int,
        homework_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> HomeworkSubmissionPage:
        return await self.read(
            f"/api/v1/homework/assignments/{homework_id}/submissions",
            HomeworkSubmissionPage,
            sender_id,
            chat_id,
            params={"skip": str(skip), "limit": str(limit)},
            error_context="homework",
        )

    async def complete_homework(
        self,
        sender_id: int,
        chat_id: int,
        homework_id: uuid.UUID,
    ) -> HomeworkCompletionResult:
        try:
            return await self.write(
                "POST",
                f"/api/v1/homework/assignments/{homework_id}/complete",
                HomeworkCompletionResult,
                sender_id,
                chat_id,
                NoContent(),
                error_context="homework",
            )
        except BackendOutcomeUnknown as exc:
            raise HomeworkOutcomeUnknown(requires_operation_key=False) from exc

    async def _delete_homework_resource(
        self,
        path: str,
        sender_id: int,
        chat_id: int,
    ) -> None:
        try:
            await self.delete(path, sender_id, chat_id, error_context="homework")
        except BackendOutcomeUnknown:
            try:
                await self.delete(path, sender_id, chat_id, error_context="homework")
            except BackendError as retry_error:
                if retry_error.status == 404:
                    raise HomeworkOutcomeUnknown(
                        requires_operation_key=False
                    ) from retry_error
                raise
            except BackendOutcomeUnknown as retry_error:
                raise HomeworkOutcomeUnknown(
                    requires_operation_key=False
                ) from retry_error
            else:
                return

    async def delete_homework_assignment(
        self,
        sender_id: int,
        chat_id: int,
        homework_id: uuid.UUID,
    ) -> None:
        await self._delete_homework_resource(
            f"/api/v1/homework/assignments/{homework_id}",
            sender_id,
            chat_id,
        )

    async def upload_homework_attachment(
        self,
        sender_id: int,
        chat_id: int,
        file_name: str,
        source_factory: Callable[[], AsyncIterable[bytes]],
        operation_key: uuid.UUID,
        *,
        advertised_size: int | None = None,
    ) -> StagedHomeworkAttachment:
        """Upload a repeatable byte source using the supplied idempotency key."""
        if advertised_size is not None:
            if advertised_size < 0:
                raise ValueError("Advertised attachment size cannot be negative")
            if advertised_size > transfer.MAX_HOMEWORK_ATTACHMENT_BYTES:
                raise HomeworkAttachmentTooLarge()
        if sender_id not in self.tokens:
            await self.session(sender_id, chat_id, error_context="homework")
        boundary = uuid.uuid4().hex

        async def send() -> StagedHomeworkAttachment:
            try:
                async with asyncio.timeout(transfer.HOMEWORK_TRANSFER_TIMEOUT_SECONDS):
                    return await self.request(
                        "POST",
                        "/api/v1/homework/attachments",
                        StagedHomeworkAttachment,
                        token=self.tokens[sender_id],
                        content=transfer._multipart_file_stream(
                            source_factory, file_name, boundary
                        ),
                        headers={
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                            "Idempotency-Key": str(operation_key),
                        },
                        timeout=httpx.Timeout(
                            transfer.HOMEWORK_TRANSFER_TIMEOUT_SECONDS
                        ),
                        error_context="homework",
                        _ambiguous_write=True,
                    )
            except TimeoutError as exc:
                raise HomeworkOutcomeUnknown() from exc
            except BackendOutcomeUnknown as exc:
                raise HomeworkOutcomeUnknown() from exc

        try:
            return await send()
        except BackendError as exc:
            if exc.status != 401:
                raise
            await self.session(sender_id, chat_id, error_context="homework")
            return await send()

    @staticmethod
    async def _bounded_error_response(response: httpx.Response) -> httpx.Response:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            remaining = transfer.MAX_HOMEWORK_ERROR_BODY_BYTES - len(body)
            if remaining <= 0:
                break
            body.extend(chunk[:remaining])
            if len(chunk) > remaining:
                break
        return httpx.Response(response.status_code, content=bytes(body))

    async def _stream_homework_attachment_once(
        self,
        path: str,
        token: str,
    ) -> AsyncIterator[bytes]:
        probe = await self.breaker.enter()
        failed = False
        try:
            async with asyncio.timeout(transfer.HOMEWORK_TRANSFER_TIMEOUT_SECONDS):
                async with self.http.stream(
                    "GET",
                    path,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=httpx.Timeout(transfer.HOMEWORK_TRANSFER_TIMEOUT_SECONDS),
                ) as response:
                    if response.status_code >= 500:
                        failed = True
                        raise BackendUnavailable()
                    if response.is_error:
                        bounded_response = await self._bounded_error_response(response)
                        raise self._backend_error(bounded_response, "homework")
                    if response.status_code != 200:
                        failed = True
                        raise BackendUnavailable()
                    length_header = response.headers.get("Content-Length")
                    try:
                        expected_size = (
                            int(length_header) if length_header is not None else None
                        )
                    except ValueError as exc:
                        failed = True
                        raise BackendUnavailable() from exc
                    if expected_size is not None and expected_size < 0:
                        failed = True
                        raise BackendUnavailable()
                    if (
                        expected_size is not None
                        and expected_size > transfer.MAX_HOMEWORK_ATTACHMENT_BYTES
                    ):
                        failed = True
                        raise HomeworkAttachmentTooLarge()
                    byte_count = 0
                    async for chunk in response.aiter_bytes():
                        byte_count += len(chunk)
                        if byte_count > transfer.MAX_HOMEWORK_ATTACHMENT_BYTES:
                            failed = True
                            raise HomeworkAttachmentTooLarge()
                        if chunk:
                            yield chunk
                    if expected_size is not None and byte_count != expected_size:
                        failed = True
                        raise BackendUnavailable()
        except httpx.TransportError as exc:
            failed = True
            raise BackendUnavailable() from exc
        except TimeoutError as exc:
            failed = True
            raise BackendUnavailable() from exc
        except asyncio.CancelledError:
            failed = True
            raise
        finally:
            await self.breaker.finish(failed, probe)

    async def download_homework_attachment(
        self,
        sender_id: int,
        chat_id: int,
        attachment_id: uuid.UUID,
    ) -> AsyncIterator[bytes]:
        if sender_id not in self.tokens:
            await self.session(sender_id, chat_id, error_context="homework")
        path = f"/api/v1/homework/attachments/{attachment_id}"
        try:
            async for chunk in self._stream_homework_attachment_once(
                path, self.tokens[sender_id]
            ):
                yield chunk
        except BackendError as exc:
            if exc.status != 401:
                raise
            await self.session(sender_id, chat_id, error_context="homework")
            async for chunk in self._stream_homework_attachment_once(
                path, self.tokens[sender_id]
            ):
                yield chunk

    async def delete_staged_homework_attachment(
        self,
        sender_id: int,
        chat_id: int,
        attachment_id: uuid.UUID,
    ) -> None:
        await self._delete_homework_resource(
            f"/api/v1/homework/attachments/{attachment_id}",
            sender_id,
            chat_id,
        )

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

    async def read_teacher_student_profiles(
        self,
        sender_id: int,
        chat_id: int,
        *,
        skip: int = 0,
        limit: int = 20,
    ) -> TeacherStudentProfilePage:
        return await self.read(
            "/api/v1/auth/teacher/students/profiles",
            TeacherStudentProfilePage,
            sender_id,
            chat_id,
            params={"skip": str(skip), "limit": str(limit)},
            error_context="settings",
        )

    async def read_former_student_profiles(
        self,
        sender_id: int,
        chat_id: int,
        *,
        skip: int = 0,
        limit: int = 20,
    ) -> TeacherStudentProfilePage:
        return await self.read(
            "/api/v1/auth/teacher/former-students",
            TeacherStudentProfilePage,
            sender_id,
            chat_id,
            params={"skip": str(skip), "limit": str(limit)},
            error_context="settings",
        )

    async def read_teacher_student_profile(
        self,
        sender_id: int,
        chat_id: int,
        student_id: uuid.UUID,
    ) -> TeacherStudentProfileDetail:
        return await self.read(
            f"/api/v1/auth/teacher/students/profiles/{student_id}",
            TeacherStudentProfileDetail,
            sender_id,
            chat_id,
            error_context="settings",
        )

    async def read_former_student_profile(
        self,
        sender_id: int,
        chat_id: int,
        relationship_id: uuid.UUID,
    ) -> TeacherStudentProfileDetail:
        return await self.read(
            f"/api/v1/auth/teacher/former-students/{relationship_id}",
            TeacherStudentProfileDetail,
            sender_id,
            chat_id,
            error_context="settings",
        )

    async def preview_teacher_student_removal(
        self,
        sender_id: int,
        chat_id: int,
        student_id: uuid.UUID,
    ) -> StudentRemovalPreview:
        return await self.write(
            "POST",
            f"/api/v1/auth/teacher/students/{student_id}/removal/preview",
            StudentRemovalPreview,
            sender_id,
            chat_id,
            NoContent(),
            error_context="settings",
            ambiguous_write=False,
        )

    async def confirm_teacher_student_removal(
        self,
        sender_id: int,
        chat_id: int,
        student_id: uuid.UUID,
        request: StudentRemovalConfirmRequest,
    ) -> StudentRemovalResult:
        try:
            return await self.write(
                "POST",
                f"/api/v1/auth/teacher/students/{student_id}/removal/confirm",
                StudentRemovalResult,
                sender_id,
                chat_id,
                request,
                headers={"Idempotency-Key": str(request.operation_key)},
                error_context="settings",
            )
        except BackendOutcomeUnknown as exc:
            raise StudentRemovalOutcomeUnknown(request) from exc

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
            error_context="settings",
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
            error_context="settings",
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
            error_context="settings",
        )

    async def read_schedule_day(
        self,
        sender_id: int,
        chat_id: int,
        requested_date: date | None = None,
    ) -> ScheduleResponse:
        return await self._request_schedule(sender_id, chat_id, "day", requested_date)

    async def read_schedule_week(
        self,
        sender_id: int,
        chat_id: int,
        requested_date: date | None = None,
    ) -> ScheduleResponse:
        return await self._request_schedule(sender_id, chat_id, "week", requested_date)

    async def read_schedule_view(
        self,
        sender_id: int,
        chat_id: int,
        period: SchedulePeriod,
        requested_date: date | None = None,
    ) -> ScheduleReadResult:
        namespace = self._backend_namespace
        try:
            account, identity = await self._read_authorized_account(
                sender_id, chat_id, error_context="schedule"
            )
        except BackendUnavailable:
            identity = self.schedule_cache.identity_for_sender(namespace, sender_id)
            if identity is None:
                raise
            cached = self._cached_schedule(identity, period, requested_date)
            if cached is None:
                raise
            return cached

        if account.role not in {"TEACHER", "STUDENT"}:
            raise BackendError(403, "This account does not have schedule access.")

        fetch_ticket = (
            self.schedule_cache.begin_fetch(identity, period)
            if identity is not None
            else None
        )
        try:
            response = await self._request_schedule(
                sender_id, chat_id, period, requested_date
            )
        except BackendError:
            self.schedule_cache.invalidate(namespace, sender_id)
            raise
        except BackendUnavailable:
            if identity is not None:
                cached = self._cached_schedule(identity, period, requested_date)
                if cached is not None:
                    return cached
            raise

        if fetch_ticket is not None:
            try:
                self.schedule_cache.store(fetch_ticket, response)
            except (ValidationError, ValueError) as exc:
                raise BackendContractError() from exc
        return ScheduleReadResult(response=response, source="live")

    async def _request_schedule(
        self,
        sender_id: int,
        chat_id: int,
        period: SchedulePeriod,
        requested_date: date | None,
    ) -> ScheduleResponse:
        params = {"date": requested_date.isoformat()} if requested_date else None
        result = await self.read(
            f"/api/v1/schedule/{period}",
            ScheduleResponse,
            sender_id,
            chat_id,
            params=params,
            error_context="schedule",
            strict_response=True,
        )
        if result.period != period:
            raise BackendContractError()
        return result

    async def _read_authorized_account(
        self,
        sender_id: int,
        chat_id: int,
        *,
        error_context: ErrorContext = "default",
    ) -> tuple[Account, ScheduleCacheIdentity | None]:
        namespace = self._backend_namespace
        ticket = self.schedule_cache.begin_authorization(namespace, sender_id)
        authorized = False
        try:
            try:
                account = await self.read(
                    "/api/v1/auth/me",
                    Account,
                    sender_id,
                    chat_id,
                    error_context=error_context,
                    strict_response=True,
                )
            except BackendError:
                self.schedule_cache.invalidate(namespace, sender_id)
                raise

            identity = self._schedule_identity(namespace, sender_id, account)
            if identity is None:
                self.schedule_cache.invalidate(namespace, sender_id)
            elif not self.schedule_cache.authorize(ticket, identity):
                raise BackendContractError()
            else:
                authorized = True
            return account, identity
        finally:
            if not authorized:
                self.schedule_cache.cancel_authorization(ticket)

    @staticmethod
    def _schedule_identity(
        namespace: str, sender_id: int, account: Account
    ) -> ScheduleCacheIdentity | None:
        if account.role == "TEACHER":
            role: Literal["TEACHER", "STUDENT"] = "TEACHER"
            teacher_id = None
        elif account.role == "STUDENT" and account.teacher_id is not None:
            role = "STUDENT"
            teacher_id = account.teacher_id
        else:
            return None
        try:
            return ScheduleCacheIdentity(
                backend_namespace=namespace,
                sender_id=sender_id,
                account_id=account.id,
                role=role,
                teacher_id=teacher_id,
                timezone=account.timezone,
            )
        except ValueError as exc:
            raise BackendContractError() from exc

    def _cached_schedule(
        self,
        identity: ScheduleCacheIdentity,
        period: SchedulePeriod,
        requested_date: date | None,
    ) -> ScheduleReadResult | None:
        local_date = requested_date or datetime.now(ZoneInfo(identity.timezone)).date()
        snapshot = self.schedule_cache.get(identity, period, local_date)
        if snapshot is None:
            return None
        return ScheduleReadResult(response=snapshot.response, source="cached")

    async def view(self, sender_id: int, chat_id: int) -> SessionView:
        account, _identity = await self._read_authorized_account(sender_id, chat_id)
        view = SessionView(account=account)
        if account.role == "TEACHER":
            try:
                view.teacher = await self.read(
                    f"/api/v1/auth/users/{account.id}/teacher-profile",
                    TeacherProfile,
                    sender_id,
                    chat_id,
                    strict_response=True,
                )
            except BackendError:
                self.schedule_cache.invalidate(self._backend_namespace, sender_id)
                raise
        elif account.role == "STUDENT":
            try:
                view.student = await self.read(
                    f"/api/v1/auth/users/{account.id}/student-profile",
                    StudentProfile,
                    sender_id,
                    chat_id,
                    strict_response=True,
                )
            except BackendError:
                self.schedule_cache.invalidate(self._backend_namespace, sender_id)
                raise
        return view

    async def login(
        self,
        sender_id: int,
        chat_id: int,
        *,
        telegram_username: str | None = None,
        sync_username: bool = False,
    ) -> SessionView | None:
        try:
            await self.session(
                sender_id,
                chat_id,
                telegram_username=telegram_username,
                sync_username=sync_username,
                strict_response=True,
            )
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
        self.telegram_usernames[sender_id] = data.telegram_username
        if sender_id in self.pending_registrations:
            # An ambiguous previous write must be resolved before another write.
            existing = await self.login(
                sender_id,
                data.telegram_private_chat_id,
                telegram_username=data.telegram_username,
                sync_username=True,
            )
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
            existing = await self.login(
                sender_id,
                data.telegram_private_chat_id,
                telegram_username=data.telegram_username,
                sync_username=True,
            )
            if existing is None:
                raise BackendUnavailable()
            self.pending_registrations.discard(sender_id)
            return existing
        except BackendError as exc:
            if exc.status != 409:
                raise
            existing = await self.login(
                sender_id,
                data.telegram_private_chat_id,
                telegram_username=data.telegram_username,
                sync_username=True,
            )
            if existing is None:
                raise
            return existing
        self.tokens[sender_id] = token.access_token
        # A committed registration with a failed subsequent read is also pending.
        self.pending_registrations.add(sender_id)
        view = await self.view(sender_id, data.telegram_private_chat_id)
        self.pending_registrations.discard(sender_id)
        return view
