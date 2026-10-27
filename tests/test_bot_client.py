import asyncio
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.auth.models import User
from backend.auth.utils import create_access_token
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.models import Homework, HomeworkSubmission
from backend.main import app
from telegram.api import (
    BackendClient,
    BackendError,
    BackendOutcomeUnknown,
    BackendUnavailable,
    CircuitBreaker,
    EventCreatePayload,
    EventWriteResult,
    HomeworkAssignmentPayload,
    HomeworkAttachmentTooLarge,
    HomeworkOutcomeUnknown,
    HomeworkSubmissionPayload,
    RecurrentEventCreatePayload,
    RecurrentEventUpdatePayload,
    RecurrentEventWriteResult,
    RecurrentOccurrenceCancelPayload,
    RecurrentOccurrenceMovePayload,
    Registration,
    ScheduleResponse,
    SelfProfileUpdate,
    StudentProfileUpdate,
    TeacherProfileUpdate,
    Token,
)
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

KEY = "test-service-key-for-bot-client-32-characters"


def _source_factory(*chunks: bytes):
    def factory():
        async def read_chunks():
            for chunk in chunks:
                yield chunk

        return read_chunks()

    return factory


def _schedule_payload(period: str, day: str) -> dict[str, object]:
    return {
        "period": period,
        "timezone": "UTC",
        "generated_at": "2026-10-19T12:00:00+00:00",
        "range_start": "2026-10-19T00:00:00+00:00",
        "range_end": "2026-10-20T00:00:00+00:00",
        "days": [
            {
                "date": day,
                "items": [
                    {
                        "source_kind": "event",
                        "source_id": "00000000-0000-0000-0000-000000000001",
                        "event_type": "LESSON",
                        "start": "2026-10-19T09:00:00+00:00",
                        "end": "2026-10-19T10:00:00+00:00",
                        "status": "past",
                        "subject_label": "Alice Example",
                    }
                ],
            }
        ],
    }


async def test_real_backend_login_restart_and_expired_token(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    data = Registration(
        telegram_user_id=5000000000,
        telegram_private_chat_id=5000000000,
        full_name="<Teacher>",
        role="TEACHER",
        timezone="UTC",
    )
    assert await api.login(data.telegram_user_id, data.telegram_private_chat_id) is None
    view = await api.register(data)
    api.tokens[data.telegram_user_id] = create_access_token(
        str(view.account.id), expires_delta=timedelta(seconds=-10)
    )
    assert (
        await api.view(data.telegram_user_id, data.telegram_private_chat_id)
    ).account.id == view.account.id
    driver = BotDriver(build_dispatcher(BackendClient(client, bot_service_key)))
    try:
        await driver.text("/start")
        assert "TEACHER" in driver.session.last_message.text
        assert "&lt;Teacher&gt;" in driver.session.last_message.text
        assert "Teacher code:" in driver.session.last_message.text
    finally:
        await driver.close()


async def test_schedule_reads_validate_real_backend_day_and_week(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    data = Registration(
        telegram_user_id=5000000000,
        telegram_private_chat_id=5000000000,
        full_name="Schedule Teacher",
        role="TEACHER",
        timezone="UTC",
    )
    await api.register(data)

    day = await api.read_schedule_day(
        data.telegram_user_id, data.telegram_private_chat_id, date(2026, 10, 19)
    )
    week = await api.read_schedule_week(
        data.telegram_user_id, data.telegram_private_chat_id, date(2026, 10, 25)
    )

    assert isinstance(day, ScheduleResponse)
    assert day.period == "day"
    assert day.days[0].date == date(2026, 10, 19)
    assert day.timezone == "UTC"
    assert week.period == "week"
    assert [entry.date for entry in week.days] == [
        date(2026, 10, 19),
        date(2026, 10, 20),
        date(2026, 10, 21),
        date(2026, 10, 22),
        date(2026, 10, 23),
        date(2026, 10, 24),
        date(2026, 10, 25),
    ]
    assert api.tokens[data.telegram_user_id]
    assert api.tokens[data.telegram_user_id] != bot_service_key


async def test_schedule_query_parameters_survive_user_token_renewal() -> None:
    requests: list[httpx.Request] = []
    schedule_payload = _schedule_payload("day", "2026-10-19")

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/schedule/day":
            if request.headers.get("Authorization") == "Bearer expired-token":
                return httpx.Response(401)
            return httpx.Response(200, json=schedule_payload)
        return httpx.Response(
            200, json={"access_token": "renewed-token", "token_type": "bearer"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "expired-token"
        result = await api.read_schedule_day(123, 456, date(2026, 10, 19))

    assert result.period == "day"
    assert result.days[0].items[0].subject_label == "Alice Example"
    schedule_requests = [
        request for request in requests if request.url.path == "/api/v1/schedule/day"
    ]
    assert [request.url.params["date"] for request in schedule_requests] == [
        "2026-10-19",
        "2026-10-19",
    ]
    assert schedule_requests[0].headers["Authorization"] == "Bearer expired-token"
    assert schedule_requests[1].headers["Authorization"] == "Bearer renewed-token"
    assert all(
        "X-Bot-Service-Key" not in request.headers for request in schedule_requests
    )
    session_request = next(
        request
        for request in requests
        if request.url.path == "/api/v1/telegram/session"
    )
    assert session_request.headers["X-Bot-Service-Key"] == KEY
    assert "Authorization" not in session_request.headers


async def test_schedule_errors_are_contextual_without_changing_onboarding_errors() -> (
    None
):
    status = [409]

    async def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status[0], json={"detail": "not displayed"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        for code, expected in {
            400: "This account cannot view a schedule.",
            403: "This account does not have schedule access.",
            404: "The linked teacher is not available for schedule viewing.",
            409: "The linked teacher cannot provide a schedule.",
            422: "The schedule request or calendar data is invalid.",
        }.items():
            status[0] = code
            with pytest.raises(BackendError) as error:
                await api.read_schedule_day(123, 456)
            assert error.value.status == code
            assert str(error.value) == expected
            assert "registered" not in str(error.value)
            assert "settings" not in str(error.value)

        status[0] = 409
        with pytest.raises(BackendError) as onboarding_error:
            await api.request("GET", "/api/v1/auth/me", Token, token="user-jwt")
        assert onboarding_error.value.status == 409
        assert str(onboarding_error.value) == "Account already registered."


async def test_schedule_session_failure_uses_schedule_specific_message() -> None:
    async def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not displayed"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        with pytest.raises(BackendError) as error:
            await api.read_schedule_day(123, 456)

    assert error.value.status == 404
    assert (
        str(error.value) == "The linked teacher is not available for schedule viewing."
    )
    assert "invitation" not in str(error.value)


async def test_schedule_server_and_malformed_responses_are_unavailable() -> None:
    status = [503]

    async def transport(_request: httpx.Request) -> httpx.Response:
        if status[0] == 0:
            return httpx.Response(200, json={})
        return httpx.Response(status[0])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(BackendUnavailable):
            await api.read_schedule_day(123, 456)
        status[0] = 0
        with pytest.raises(BackendUnavailable):
            await api.read_schedule_day(123, 456)


async def test_typed_management_reads_writes_profiles_and_204_delete(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher_id = 5000000001
    student_id = 5000000002
    teacher = await api.register(
        Registration(
            telegram_user_id=teacher_id,
            telegram_private_chat_id=teacher_id,
            full_name="Management Teacher",
            role="TEACHER",
            timezone="UTC",
        )
    )
    student = await api.register(
        Registration(
            telegram_user_id=student_id,
            telegram_private_chat_id=student_id,
            full_name="Management Student",
            role="STUDENT",
            timezone="UTC",
            teacher_code=teacher.teacher.code,
        )
    )

    picker = await api.read_student_picker(teacher_id, teacher_id)
    assert picker.total == 1
    assert picker.items[0].id == student.account.id

    lesson = await api.write_management_event(
        "POST",
        "lessons",
        teacher_id,
        teacher_id,
        EventCreatePayload(
            start="2035-01-08T10:00:00Z",
            end="2035-01-08T11:00:00Z",
            user_id=student.account.id,
            event_type="LESSON",
        ),
        EventWriteResult,
    )
    page = await api.read_management_page(teacher_id, teacher_id, "lessons")
    assert page.total == 1
    assert page.items[0].id == lesson.id

    detail = await api.read_management_detail(
        teacher_id, teacher_id, "lessons", lesson.id
    )
    assert detail.revision
    await api.delete_management_event(
        "lessons", lesson.id, teacher_id, teacher_id, revision=detail.revision
    )
    assert (
        await api.read_management_page(teacher_id, teacher_id, "lessons")
    ).total == 0

    series = await api.write_management_event(
        "POST",
        "recurrent-lessons",
        teacher_id,
        teacher_id,
        RecurrentEventCreatePayload(
            start="2035-01-08T10:00:00Z",
            end="2035-01-08T11:00:00Z",
            user_id=student.account.id,
            event_type="LESSON",
            interval=7,
            interval_end="2035-01-22T10:00:00Z",
        ),
        RecurrentEventWriteResult,
    )
    moved = await api.move_recurrent_occurrence(
        teacher_id,
        teacher_id,
        series.id,
        RecurrentOccurrenceMovePayload(
            occurrence_start="2035-01-08T10:00:00Z",
            occurrence_end="2035-01-08T11:00:00Z",
            replacement_start="2035-01-09T10:00:00Z",
            replacement_end="2035-01-09T11:00:00Z",
        ),
    )
    assert moved.replacement is not None
    cancelled = await api.cancel_recurrent_occurrence(
        teacher_id,
        teacher_id,
        series.id,
        RecurrentOccurrenceCancelPayload(
            occurrence_start="2035-01-15T10:00:00Z",
            occurrence_end="2035-01-15T11:00:00Z",
        ),
    )
    assert cancelled.cancelled_occurrence.start.isoformat().startswith("2035-01-15")
    series_detail = await api.read_management_detail(
        teacher_id, teacher_id, "recurrent-lessons", series.id
    )
    assert series_detail.cancelled_occurrences == 2

    open_series = await api.write_management_event(
        "POST",
        "recurrent-lessons",
        teacher_id,
        teacher_id,
        RecurrentEventCreatePayload(
            start="2035-02-05T10:00:00Z",
            end="2035-02-05T11:00:00Z",
            user_id=student.account.id,
            event_type="LESSON",
            interval=7,
            interval_end=None,
        ),
        RecurrentEventWriteResult,
    )
    assert open_series.interval_end is None
    open_detail = await api.read_management_detail(
        teacher_id, teacher_id, "recurrent-lessons", open_series.id
    )
    assert open_detail.interval_end is None

    updated_open_series = await api.write_management_event(
        "PUT",
        "recurrent-lessons",
        teacher_id,
        teacher_id,
        RecurrentEventUpdatePayload(
            start=open_detail.start,
            end=open_detail.end,
            user_id=open_detail.user_id,
            event_type="LESSON",
            interval=7,
            interval_end=None,
        ),
        RecurrentEventWriteResult,
        event_id=open_detail.id,
        revision=open_detail.revision,
    )
    assert updated_open_series.interval_end is None

    with pytest.raises(ValueError, match="Only weekly lessons"):
        RecurrentEventCreatePayload(
            start="2035-03-05T10:00:00Z",
            end="2035-03-05T11:00:00Z",
            user_id=student.account.id,
            event_type="BREAK",
            interval=7,
            interval_end=None,
        )

    teacher_profile = await api.update_teacher_profile(
        teacher_id,
        teacher_id,
        teacher.account.id,
        TeacherProfileUpdate(work_start="08:30"),
    )
    assert teacher_profile.work_start == "08:30"

    account = await api.update_me(
        student_id,
        student_id,
        SelfProfileUpdate(notification_lessons_today=None),
    )
    assert account.id == student.account.id
    student_profile = await api.update_student_profile(
        student_id,
        student_id,
        student.account.id,
        StudentProfileUpdate(notification_lesson=None),
    )
    assert student_profile.notification_lesson is None


async def test_lost_event_create_response_is_reconciled_by_read_not_replayed(
    client, bot_service_key
) -> None:
    create_attempts = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal create_attempts
        response = await client.send(request)
        if request.method == "POST" and request.url.path == "/api/v1/lessons/":
            create_attempts += 1
            raise httpx.ReadTimeout("response lost after commit", request=request)
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, bot_service_key)
        teacher_id = 5000000010
        teacher = await api.register(
            Registration(
                telegram_user_id=teacher_id,
                telegram_private_chat_id=teacher_id,
                full_name="Uncertain Write Teacher",
                role="TEACHER",
                timezone="UTC",
            )
        )
        with pytest.raises(BackendOutcomeUnknown):
            await api.write_management_event(
                "POST",
                "lessons",
                teacher_id,
                teacher_id,
                EventCreatePayload(
                    start="2035-01-08T10:00:00Z",
                    end="2035-01-08T11:00:00Z",
                    user_id=teacher.account.id,
                    event_type="LESSON",
                ),
                EventWriteResult,
            )

        page = await api.read_management_page(teacher_id, teacher_id, "lessons")

    assert create_attempts == 1
    assert page.total == 1


class _WritePayload(BaseModel):
    name: str


async def test_write_retries_once_only_after_definitive_401() -> None:
    requests: list[httpx.Request] = []
    write_count = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal write_count
        requests.append(request)
        if request.url.path == "/api/v1/telegram/session":
            return httpx.Response(
                200, json={"access_token": "renewed", "token_type": "bearer"}
            )
        write_count += 1
        if write_count == 1:
            return httpx.Response(401, json={"detail": "not displayed"})
        return httpx.Response(
            200, json={"access_token": "saved", "token_type": "bearer"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "expired-token"
        result = await api.write(
            "POST",
            "/api/v1/lessons/",
            Token,
            123,
            456,
            _WritePayload(name="lesson"),
            revision="a" * 64,
            displacement_token="b" * 64,
            confirm_displacement=True,
        )

    assert result.access_token == "saved"
    writes = [request for request in requests if request.url.path == "/api/v1/lessons/"]
    assert len(writes) == 2
    assert writes[0].headers["If-Match"] == "a" * 64
    assert writes[0].headers["X-Displacement-Token"] == "b" * 64
    assert writes[0].url.params["confirm_displacement"] == "true"
    assert writes[0].content == b'{"name":"lesson"}'
    assert (
        sum(request.url.path == "/api/v1/telegram/session" for request in requests) == 1
    )


@pytest.mark.parametrize("failure", ["transport", "server"])
async def test_ambiguous_write_is_not_replayed(failure: str) -> None:
    calls = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "transport":
            raise httpx.ReadTimeout("private transport details", request=request)
        return httpx.Response(503, json={"detail": "private server details"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(BackendOutcomeUnknown) as error:
            await api.write(
                "POST",
                "/api/v1/lessons/",
                Token,
                123,
                456,
                _WritePayload(name="lesson"),
            )

    assert calls == 1
    assert "outcome is unknown" in str(error.value)
    assert "Reload the latest data" in str(error.value)
    assert "private" not in str(error.value)


async def test_scheduling_errors_are_safe_and_displacement_preview_is_typed() -> None:
    payload = {
        "detail": {
            "code": "break_displacement_confirmation_required",
            "message": "Confirm cancellation of overlapping lesson occurrences",
            "confirmation_token": "a" * 64,
            "affected_occurrences": [
                {
                    "source_kind": "recurrent_event",
                    "source_id": "00000000-0000-0000-0000-000000000001",
                    "user_id": "00000000-0000-0000-0000-000000000002",
                    "event_type": "LESSON",
                    "start": "2035-01-08T10:00:00Z",
                    "end": "2035-01-08T11:00:00Z",
                    "automatic_break": {
                        "start": "2035-01-08T11:00:00Z",
                        "end": "2035-01-08T11:05:00Z",
                        "duration_minutes": 5,
                    },
                }
            ],
        }
    }
    response = [httpx.Response(409, json=payload)]

    async def transport(_request: httpx.Request) -> httpx.Response:
        return response[0]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(BackendError) as preview_error:
            await api.read(
                "/api/v1/breaks/",
                EventWriteResult,
                123,
                456,
                error_context="scheduling",
            )
        preview = preview_error.value.displacement_preview
        assert preview is not None
        assert preview.code == "break_displacement_confirmation_required"
        assert len(preview.affected_occurrences) == 1
        assert preview.affected_occurrences[0].automatic_break is not None
        assert preview.affected_occurrences[0].automatic_break.duration_minutes == 5

        response[0] = httpx.Response(
            409,
            json={
                "detail": {
                    "code": "break_displacement_confirmation_required",
                    "message": "arbitrary message",
                    "confirmation_token": "not-a-token",
                    "affected_occurrences": [],
                }
            },
        )
        with pytest.raises(BackendError) as invalid_preview_error:
            await api.read(
                "/api/v1/breaks/",
                EventWriteResult,
                123,
                456,
                error_context="scheduling",
            )
        assert invalid_preview_error.value.displacement_preview is None
        assert invalid_preview_error.value.code is None
        assert "arbitrary message" not in str(invalid_preview_error.value)

        response[0] = httpx.Response(
            409,
            json={"detail": {"code": "stale_event_revision", "message": "private"}},
        )
        with pytest.raises(BackendError) as stale_error:
            await api.read(
                "/api/v1/lessons/1",
                EventWriteResult,
                123,
                456,
                error_context="scheduling",
            )
        assert stale_error.value.code == "stale_event_revision"
        assert str(stale_error.value) == "This event changed. Reload it before editing."
        assert "private" not in str(stale_error.value)

        response[0] = httpx.Response(
            409, json={"detail": "Schedule is being changed; retry this request."}
        )
        with pytest.raises(BackendError) as contention_error:
            await api.read(
                "/api/v1/lessons/",
                EventWriteResult,
                123,
                456,
                error_context="scheduling",
            )
        assert contention_error.value.displacement_preview is None
        assert "being changed" in str(contention_error.value)

        response[0] = httpx.Response(
            422, json={"detail": "Lesson starts before the teacher's work hours"}
        )
        with pytest.raises(BackendError) as policy_error:
            await api.read(
                "/api/v1/lessons/",
                EventWriteResult,
                123,
                456,
                error_context="scheduling",
            )
        assert (
            str(policy_error.value)
            == "That time is before the teacher's working hours."
        )


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (
            422,
            "Teacher working days are not configured",
            "The teacher has not configured working days.",
        ),
        (
            422,
            "Students must use the teacher's configured lesson duration",
            "Use the lesson duration configured by the teacher.",
        ),
        (
            422,
            "Teacher's daily lesson limit of 6 would be exceeded",
            "The teacher's daily lesson limit would be exceeded.",
        ),
        (
            422,
            "Teacher's consecutive lesson limit of 4 would be exceeded",
            "The teacher's consecutive lesson limit would be exceeded.",
        ),
        (
            422,
            "Event overlaps an existing event",
            "That time overlaps another scheduled event.",
        ),
    ],
)
def test_known_booking_policy_errors_are_safely_mapped(
    status: int, detail: str, expected: str
) -> None:
    error = BackendClient._backend_error(
        httpx.Response(status, json={"detail": detail}), "scheduling"
    )
    assert str(error) == expected
    assert detail not in str(error)


async def test_breaker_opens_and_allows_one_half_open_probe() -> None:
    now = [0.0]
    calls = []
    blocked = asyncio.Event()
    probe_started = asyncio.Event()
    healthy = False

    async def transport(request):
        calls.append(request)
        if not healthy:
            return httpx.Response(503)
        probe_started.set()
        await blocked.wait()
        return httpx.Response(
            200, json={"access_token": "test-jwt", "token_type": "bearer"}
        )

    breaker = CircuitBreaker(2, 5, lambda: now[0])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test", timeout=2
    ) as http:
        api = BackendClient(http, KEY, breaker)
        for _ in range(3):
            with pytest.raises(BackendUnavailable):
                await api.session(1, 1)
        assert len(calls) == 2
        now[0] = 6
        healthy = True
        probe = asyncio.create_task(api.session(1, 1))
        await probe_started.wait()
        with pytest.raises(BackendUnavailable):
            await api.session(2, 2)
        blocked.set()
        await probe
        assert breaker.opened_at is None and breaker.failures == 0
        assert len(calls) == 3


async def test_transport_failure_errors_and_credential_separation() -> None:
    status = [404]
    requests = []

    async def transport(request):
        requests.append(request)
        if status[0] == 0:
            raise httpx.ReadTimeout("local timeout", request=request)
        return httpx.Response(status[0], json={"detail": "not displayed"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test", timeout=2
    ) as http:
        api = BackendClient(http, KEY)
        assert await api.login(1, 1) is None
        for code in [400, 401, 403, 422]:
            status[0] = code
            with pytest.raises(BackendError) as error:
                await api.session(1, 1)
            assert error.value.status == code
            assert api.breaker.failures == 0
        status[0] = 0
        with pytest.raises(BackendUnavailable):
            await api.session(1, 1)
        assert api.breaker.failures == 1
        status[0] = 403
        with pytest.raises(BackendError):
            await api.request("GET", "/api/v1/auth/me", Token, token="user-jwt")
        assert "X-Bot-Service-Key" not in requests[-1].headers
        assert requests[-1].headers["Authorization"] == "Bearer user-jwt"
        assert "Authorization" not in requests[0].headers
        assert requests[0].headers["X-Bot-Service-Key"] == KEY


async def test_lost_registration_response_resolves_without_replay(
    client, bot_service_key
) -> None:
    calls = []

    async def transport(request):
        calls.append(request.url.path)
        response = await client.send(request)
        if request.url.path.endswith("/register"):
            raise httpx.ReadTimeout("response lost after commit", request=request)
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test", timeout=2
    ) as http:
        api = BackendClient(http, bot_service_key)
        view = await api.register(
            Registration(
                telegram_user_id=5000000000,
                telegram_private_chat_id=5000000000,
                full_name="Teacher",
                role="TEACHER",
                timezone="UTC",
            )
        )
        assert view.account.role == "TEACHER"
        assert calls.count("/api/v1/telegram/register") == 1
        assert "/api/v1/telegram/session" in calls


async def test_outage_screen_does_not_claim_login() -> None:
    async def transport(request):
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test", timeout=2
    ) as http:
        driver = BotDriver(build_dispatcher(BackendClient(http, KEY)))
        try:
            await driver.text("/start")
            assert "Backend unavailable" in driver.session.last_message.text
            assert "No offline account" in driver.session.last_message.text
        finally:
            await driver.close()


async def test_homework_client_streams_backend_workflow(
    client: httpx.AsyncClient,
    db_session,
    tmp_path: Path,
) -> None:
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=tmp_path
    )
    teacher = User(
        id=uuid4(),
        email="homework-client-teacher@example.com",
        role="TEACHER",
        timezone="UTC",
    )
    student = User(
        id=uuid4(),
        email="homework-client-student@example.com",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
    )
    db_session.add_all([teacher, student])
    await db_session.commit()

    async def transport(request: httpx.Request) -> httpx.Response:
        return await client.send(request)

    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport), base_url="http://test"
        ) as http:
            api = BackendClient(http, KEY)
            api.tokens[100] = create_access_token(str(teacher.id))
            api.tokens[200] = create_access_token(str(student.id))
            operation_key = uuid4()
            staged = await api.upload_homework_attachment(
                100,
                100,
                "../worksheet.pdf",
                _source_factory(b"worksheet ", b"contents"),
                operation_key,
                advertised_size=len(b"worksheet contents"),
            )
            assert staged.id == operation_key
            assert staged.file_name == "worksheet.pdf"
            assert staged.byte_size == len(b"worksheet contents")

            assignment = await api.create_homework_assignment(
                100,
                100,
                HomeworkAssignmentPayload(
                    student_id=student.id,
                    text="Read the material",
                    attachment_ids=[staged.id],
                ),
                uuid4(),
            )
            teacher_page = await api.read_homework_assignments(100, 100)
            student_page = await api.read_homework_assignments(200, 200)
            assert teacher_page.total == student_page.total == 1
            assert teacher_page.items[0].id == assignment.id
            assert student_page.items[0].id == assignment.id
            detail = await api.read_homework_assignment(200, 200, assignment.id)
            assert detail.materials[0].id == staged.id

            downloaded = b"".join(
                [
                    chunk
                    async for chunk in api.download_homework_attachment(
                        200, 200, staged.id
                    )
                ]
            )
            assert downloaded == b"worksheet contents"

            submitted = await api.submit_homework(
                200,
                200,
                assignment.id,
                HomeworkSubmissionPayload(
                    text="I finished",
                    operation_key=uuid4(),
                ),
            )
            assert submitted.version == 1
            assert submitted.assignment_status == "COMPLETE"
            assert submitted.completion_source == "STUDENT_SUBMISSION"
            reviews = await api.read_homework_submissions(100, 100, assignment.id)
            assert reviews.total == 1
            assert reviews.items[0].text == "I finished"
            assert reviews.items[0].is_current is True

            manual_assignment = await api.create_homework_assignment(
                100,
                100,
                HomeworkAssignmentPayload(
                    student_id=student.id,
                    text="Mark this one complete",
                ),
                uuid4(),
            )
            completed = await api.complete_homework(100, 100, manual_assignment.id)
            assert completed.status == "COMPLETE"
            assert completed.completion_source == "TEACHER"

            await api.delete_homework_assignment(100, 100, manual_assignment.id)
            await api.delete_homework_assignment(100, 100, assignment.id)
            assert not (tmp_path / operation_key.hex).exists()
    finally:
        app.dependency_overrides.pop(get_homework_settings, None)


async def test_homework_submission_unknown_outcome_retries_same_operation_key(
    client: httpx.AsyncClient,
    db_session,
) -> None:
    teacher = User(
        id=uuid4(),
        email="homework-retry-teacher@example.com",
        role="TEACHER",
        timezone="UTC",
    )
    student = User(
        id=uuid4(),
        email="homework-retry-student@example.com",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
    )
    homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text="Complete the exercise",
        operation_key=uuid4(),
    )
    db_session.add_all([teacher, student, homework])
    await db_session.commit()
    submissions = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal submissions
        response = await client.send(request)
        if request.url.path.endswith("/submissions"):
            submissions += 1
            if submissions == 1:
                raise httpx.ReadTimeout("lost after commit", request=request)
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[200] = create_access_token(str(student.id))
        payload = HomeworkSubmissionPayload(
            text="My solution",
            operation_key=uuid4(),
        )
        with pytest.raises(HomeworkOutcomeUnknown):
            await api.submit_homework(200, 200, homework.id, payload)

        result = await api.submit_homework(200, 200, homework.id, payload)

    assert result.version == 1
    assert submissions == 2
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(HomeworkSubmission)
            .where(HomeworkSubmission.homework_id == homework.id)
        )
        == 1
    )


async def test_homework_upload_renews_jwt_and_recreates_stream() -> None:
    requests: list[httpx.Request] = []
    uploaded: list[bytes] = []
    sources_created = 0
    attachment_id = uuid4()
    operation_key = uuid4()

    def source_factory():
        nonlocal sources_created
        sources_created += 1

        async def chunks():
            yield b"file contents"

        return chunks()

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/telegram/session":
            return httpx.Response(
                200, json={"access_token": "renewed-token", "token_type": "bearer"}
            )
        if request.headers["Authorization"] == "Bearer expired-token":
            return httpx.Response(401, json={"detail": "private"})
        uploaded.append(await request.aread())
        return httpx.Response(
            201,
            json={
                "id": str(attachment_id),
                "file_name": "document.txt",
                "byte_size": len(b"file contents"),
                "expires_at": "2026-10-26T12:00:00+00:00",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "expired-token"
        staged = await api.upload_homework_attachment(
            123,
            456,
            "document.txt",
            source_factory,
            operation_key,
        )

    assert staged.id == attachment_id
    assert sources_created == 2
    assert len(uploaded) == 1
    assert b"file contents" in uploaded[0]
    assert b'filename="document.txt"' in uploaded[0]
    upload_requests = [
        request
        for request in requests
        if request.url.path == "/api/v1/homework/attachments"
    ]
    assert len(upload_requests) == 2
    assert upload_requests[0].headers["Authorization"] == "Bearer expired-token"
    assert upload_requests[1].headers["Authorization"] == "Bearer renewed-token"
    assert all(
        request.headers["Idempotency-Key"] == str(operation_key)
        for request in upload_requests
    )
    assert all(
        "X-Bot-Service-Key" not in request.headers for request in upload_requests
    )
    session_request = next(
        request
        for request in requests
        if request.url.path == "/api/v1/telegram/session"
    )
    assert session_request.headers["X-Bot-Service-Key"] == KEY
    assert "Authorization" not in session_request.headers


async def test_homework_attachment_upload_enforces_actual_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("telegram.api.MAX_HOMEWORK_ATTACHMENT_BYTES", 5)
    attempts = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        await request.aread()
        return httpx.Response(201, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(HomeworkAttachmentTooLarge):
            await api.upload_homework_attachment(
                123,
                456,
                "large.bin",
                _source_factory(b"123", b"456"),
                uuid4(),
                advertised_size=5,
            )

    assert attempts == 0


async def test_homework_download_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("telegram.api.MAX_HOMEWORK_ATTACHMENT_BYTES", 5)

    async def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "5"}, content=b"123456")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(HomeworkAttachmentTooLarge):
            _ = [
                chunk
                async for chunk in api.download_homework_attachment(123, 456, uuid4())
            ]


async def test_homework_transfer_timeout_is_unknown_for_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("telegram.api.HOMEWORK_TRANSFER_TIMEOUT_SECONDS", 0.01)

    async def slow_source():
        await asyncio.sleep(0.1)
        yield b"late"

    async def transport(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(201, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(HomeworkOutcomeUnknown):
            await api.upload_homework_attachment(
                123, 456, "slow.bin", slow_source, uuid4()
            )


async def test_homework_download_timeout_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("telegram.api.HOMEWORK_TRANSFER_TIMEOUT_SECONDS", 0.01)

    class SlowResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.1)
            yield b"late"

    async def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "4"},
            stream=SlowResponse(),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(BackendUnavailable):
            _ = [
                chunk
                async for chunk in api.download_homework_attachment(123, 456, uuid4())
            ]


async def test_malformed_homework_mutation_response_is_unknown() -> None:
    attempts = 0

    async def transport(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(201, json={"version": 1})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(HomeworkOutcomeUnknown):
            await api.submit_homework(
                123,
                456,
                uuid4(),
                HomeworkSubmissionPayload(
                    text="My solution",
                    operation_key=uuid4(),
                ),
            )

    assert attempts == 1


async def test_homework_delete_unknown_and_stale_retry_stays_unknown() -> None:
    attempts = 0

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("lost delete response", request=request)
        return httpx.Response(404, json={"detail": "private"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, KEY)
        api.tokens[123] = "user-jwt"
        with pytest.raises(HomeworkOutcomeUnknown) as error:
            await api.delete_homework_assignment(123, 456, uuid4())

    assert attempts == 2
    assert "Reload the latest homework state" in str(error.value)


async def test_homework_errors_hide_backend_details() -> None:
    error = BackendClient._backend_error(
        httpx.Response(404, json={"detail": "private assignment information"}),
        "homework",
    )
    assert error.status == 404
    assert str(error) == (
        "This homework or attachment is no longer available. Reload the homework list."
    )
