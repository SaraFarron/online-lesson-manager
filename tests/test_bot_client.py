import asyncio
from datetime import timedelta

import httpx
import pytest

from backend.auth.utils import create_access_token
from telegram.api import (
    BackendClient,
    BackendError,
    BackendUnavailable,
    CircuitBreaker,
    Registration,
    Token,
)
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

KEY = "test-service-key-for-bot-client-32-characters"


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
