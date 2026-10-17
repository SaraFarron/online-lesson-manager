import asyncio
from datetime import date, timedelta

import httpx
import pytest

from backend.auth.utils import create_access_token
from telegram.api import (
    BackendClient,
    BackendError,
    BackendUnavailable,
    CircuitBreaker,
    Registration,
    ScheduleResponse,
    Token,
)
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

KEY = "test-service-key-for-bot-client-32-characters"


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
