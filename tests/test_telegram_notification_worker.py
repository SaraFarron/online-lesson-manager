import asyncio
import time
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from httpx import AsyncClient

from telegram.api.models.notifications import (
    AcknowledgementOutcome,
    AcknowledgeRequest,
    AcknowledgeResponse,
    ClaimedNotification,
    ClaimResponse,
    NotificationType,
)
from telegram.api.notifications import NotificationClient, NotificationProtocolError
from telegram.config import BotConfig
from telegram.notifications.rate_limit import OutboundRateLimiter
from telegram.notifications.worker import NotificationWorker
from tests.bot_support import RecordingSession

type ClaimResult = ClaimResponse | Exception


class FakeNotificationClient:
    def __init__(self, *claims: ClaimResult) -> None:
        self.claims = deque(claims)
        self.claim_count = 0
        self.acknowledgements: list[tuple[uuid.UUID, AcknowledgeRequest]] = []
        self.acknowledged = asyncio.Event()
        self.acknowledgement_error: Exception | None = None

    async def claim(self, *, limit: int = 20) -> ClaimResponse:
        assert limit == 1
        self.claim_count += 1
        if self.claims:
            result = self.claims.popleft()
            if isinstance(result, Exception):
                raise result
            return result
        return ClaimResponse(items=[])

    async def acknowledge(
        self, intent_id: uuid.UUID, request: AcknowledgeRequest
    ) -> AcknowledgeResponse:
        self.acknowledgements.append((intent_id, request))
        self.acknowledged.set()
        if self.acknowledgement_error is not None:
            raise self.acknowledgement_error
        return AcknowledgeResponse(
            id=intent_id,
            state="SENT",
            last_outcome="SENT",
            available_at=datetime.now(UTC),
        )


class FailingRecordingSession(RecordingSession):
    def __init__(self, failure: Exception) -> None:
        super().__init__()
        self.failure = failure

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        if method.__api_method__ == "sendMessage":
            raise self.failure
        return await super().make_request(bot, method, timeout)


class GatedRecordingSession(RecordingSession):
    def __init__(self) -> None:
        super().__init__()
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        if getattr(method, "__api_method__", None) == "sendMessage":
            self.send_started.set()
            await self.release_send.wait()
        return await super().make_request(bot, method, timeout)


def _notification(
    notification_type: NotificationType = "ASSIGNMENT_CREATED",
) -> ClaimedNotification:
    now = datetime.now(UTC)
    return ClaimedNotification(
        id=uuid.uuid4(),
        recipient_id=uuid.uuid4(),
        chat_id=5000000000,
        notification_type=notification_type,
        source_id=None,
        source_version=None,
        source_date=None,
        occurrence_source_type=None,
        occurrence_source_id=None,
        occurrence_start_at=None,
        scheduled_at=now,
        lease_token=uuid.uuid4(),
        lease_expires_at=now + timedelta(seconds=60),
    )


async def _start_worker_once(
    worker: NotificationWorker, client: FakeNotificationClient
) -> None:
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(client.acknowledged.wait(), timeout=1)
    worker.stop()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.parametrize(
    ("failure", "outcome", "error_code", "retry_after"),
    [
        (
            TelegramRetryAfter(
                method=SendMessage(chat_id=1, text="x"),
                message="Too Many Requests",
                retry_after=17,
            ),
            "retryable",
            "telegram_rate_limited",
            17,
        ),
        (
            TelegramRetryAfter(
                method=SendMessage(chat_id=1, text="x"),
                message="Too Many Requests",
                retry_after=86401,
            ),
            "terminal",
            "telegram_retry_after_too_long",
            None,
        ),
        (
            TelegramForbiddenError(
                method=SendMessage(chat_id=1, text="x"), message="bot was blocked"
            ),
            "terminal",
            "telegram_forbidden",
            None,
        ),
        (
            TelegramBadRequest(
                method=SendMessage(chat_id=1, text="x"), message="chat not found"
            ),
            "terminal",
            "telegram_chat_not_found",
            None,
        ),
        (
            TelegramBadRequest(
                method=SendMessage(chat_id=1, text="x"), message="invalid text"
            ),
            "terminal",
            "telegram_bad_request",
            None,
        ),
        (
            TelegramNetworkError(
                method=SendMessage(chat_id=1, text="x"), message="connection lost"
            ),
            "unknown",
            "telegram_send_unknown",
            None,
        ),
    ],
)
async def test_worker_acknowledges_delivery_failures(
    failure: Exception,
    outcome: AcknowledgementOutcome,
    error_code: str,
    retry_after: int | None,
) -> None:
    item = _notification()
    client = FakeNotificationClient(ClaimResponse(items=[item]))
    session = FailingRecordingSession(failure)
    bot = Bot("123456789:test-token-for-local-recording", session=session)
    worker = NotificationWorker(bot, client, idle_wait_seconds=0.01)
    try:
        await _start_worker_once(worker, client)
        assert client.acknowledgements == [
            (
                item.id,
                AcknowledgeRequest(
                    lease_token=item.lease_token,
                    outcome=outcome,
                    error_code=error_code,
                    retry_after_seconds=retry_after,
                ),
            )
        ]
    finally:
        await bot.session.close()


async def test_worker_sends_and_acknowledges_successful_notification() -> None:
    item = _notification()
    client = FakeNotificationClient(ClaimResponse(items=[item]))
    session = RecordingSession()
    bot = Bot("123456789:test-token-for-local-recording", session=session)
    worker = NotificationWorker(bot, client, idle_wait_seconds=0.01)
    try:
        await _start_worker_once(worker, client)
        sent = [call for call in session.calls if call.__api_method__ == "sendMessage"]
        assert len(sent) == 1
        assert sent[0].model_dump()["chat_id"] == item.chat_id
        assert sent[0].model_dump()["text"].startswith("You have new homework.")
        assert client.acknowledgements[0][1].outcome == "sent"
    finally:
        await bot.session.close()


async def test_worker_continues_after_backend_outage_and_empty_queue_is_bounded() -> (
    None
):
    client = FakeNotificationClient(httpx.ConnectError("backend unavailable"))
    session = RecordingSession()
    bot = Bot("123456789:test-token-for-local-recording", session=session)
    worker = NotificationWorker(
        bot, client, idle_wait_seconds=0.03, failure_wait_seconds=0.01
    )
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(worker.ready.wait(), timeout=1)
        await asyncio.sleep(0.08)
        worker.stop()
        await asyncio.wait_for(task, timeout=1)
        assert client.claim_count in range(3, 6)
    finally:
        worker.stop()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await bot.session.close()


async def test_shutdown_finishes_the_current_claim_before_stopping() -> None:
    item = _notification()
    client = FakeNotificationClient(ClaimResponse(items=[item]))
    session = GatedRecordingSession()
    bot = Bot("123456789:test-token-for-local-recording", session=session)
    worker = NotificationWorker(bot, client, idle_wait_seconds=0.01)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(session.send_started.wait(), timeout=1)
        worker.stop()
        session.release_send.set()
        await asyncio.wait_for(task, timeout=1)
        assert len(client.acknowledgements) == 1
        assert client.acknowledgements[0][0] == item.id
    finally:
        worker.stop()
        session.release_send.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await bot.session.close()


async def test_cancellation_leaves_claim_for_lease_recovery() -> None:
    item = _notification()
    client = FakeNotificationClient(ClaimResponse(items=[item]))
    session = GatedRecordingSession()
    bot = Bot("123456789:test-token-for-local-recording", session=session)
    worker = NotificationWorker(bot, client, idle_wait_seconds=0.01)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(session.send_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.acknowledgements
    finally:
        session.release_send.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await bot.session.close()


async def test_acknowledgement_outage_leaves_lease_for_recovery() -> None:
    item = _notification()
    client = FakeNotificationClient(ClaimResponse(items=[item]))
    client.acknowledgement_error = httpx.ReadTimeout("response lost")
    session = RecordingSession()
    bot = Bot("123456789:test-token-for-local-recording", session=session)
    worker = NotificationWorker(bot, client, idle_wait_seconds=0.01)
    try:
        await _start_worker_once(worker, client)
        assert len(client.acknowledgements) == 1
    finally:
        await bot.session.close()


async def test_bot_config_rejects_reused_service_credentials() -> None:
    shared = "a-distinct-secret-value-with-32-characters"
    with pytest.raises(ValueError, match="credentials must differ"):
        BotConfig(
            TOKEN="123456789:test-token",
            SERVICE_CREDENTIAL=shared,
            NOTIFICATION_SERVICE_CREDENTIAL=shared,
        )


async def test_outbound_rate_limiter_is_shared_by_the_bot_session() -> None:
    session = RecordingSession()
    bot = Bot("123456789:test-token-for-local-recording", session=session)
    bot.session.middleware.register(OutboundRateLimiter(requests_per_second=50))
    try:
        start = time.monotonic()
        await asyncio.gather(
            bot.send_message(1, "one"),
            bot.send_message(1, "two"),
            bot.send_message(1, "three"),
        )
        assert time.monotonic() - start >= 0.035
        assert len(session.calls) == 3
    finally:
        await bot.session.close()


async def test_notification_client_uses_its_separate_service_header() -> None:
    seen_headers: list[httpx.Headers] = []
    now = datetime.now(UTC)
    item = _notification()

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        if request.url.path.endswith("/claim"):
            return httpx.Response(
                200,
                json={"items": [item.model_dump(mode="json")]},
            )
        return httpx.Response(
            200,
            json={
                "id": str(item.id),
                "state": "SENT",
                "last_outcome": "SENT",
                "available_at": now.isoformat(),
            },
        )

    async with httpx.AsyncClient(
        base_url="http://backend",
        transport=httpx.MockTransport(handler),
    ) as http:
        client = NotificationClient(http, "notification-service-secret")
        claimed = await client.claim()
        ack = await client.acknowledge(
            item.id,
            AcknowledgeRequest(lease_token=item.lease_token, outcome="sent"),
        )

    assert claimed.items == [item]
    assert ack.id == item.id
    assert all(
        headers["X-Notification-Service-Key"] == "notification-service-secret"
        for headers in seen_headers
    )
    assert all("X-Bot-Service-Key" not in headers for headers in seen_headers)


async def test_notification_client_rejects_malformed_service_responses() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(
        base_url="http://backend",
        transport=httpx.MockTransport(handler),
    ) as http:
        client = NotificationClient(http, "notification-service-secret")
        with pytest.raises(
            NotificationProtocolError, match="claim response was invalid"
        ):
            await client.claim()


async def test_backend_health_route_supports_worker_startup_checks(
    client: AsyncClient,
) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
