import asyncio
import logging
from typing import Protocol
from uuid import UUID

import httpx
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from pydantic import ValidationError

from telegram.api.models.notifications import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    ClaimedNotification,
    ClaimResponse,
)
from telegram.api.notifications import NotificationProtocolError
from telegram.notifications.messages import render_notification

logger = logging.getLogger(__name__)

CLAIM_BATCH_LIMIT = 1
EMPTY_QUEUE_WAIT_SECONDS = 5
BACKEND_FAILURE_WAIT_SECONDS = 5


class NotificationTransport(Protocol):
    async def claim(self, *, limit: int = 20) -> ClaimResponse: ...

    async def acknowledge(
        self, intent_id: UUID, request: AcknowledgeRequest
    ) -> AcknowledgeResponse: ...


class NotificationWorker:
    def __init__(
        self,
        bot: Bot,
        client: NotificationTransport,
        *,
        idle_wait_seconds: float = EMPTY_QUEUE_WAIT_SECONDS,
        failure_wait_seconds: float = BACKEND_FAILURE_WAIT_SECONDS,
    ) -> None:
        if idle_wait_seconds <= 0 or failure_wait_seconds <= 0:
            raise ValueError("Notification worker wait intervals must be positive")
        self.bot = bot
        self.client = client
        self.idle_wait_seconds = idle_wait_seconds
        self.failure_wait_seconds = failure_wait_seconds
        self.stop_event = asyncio.Event()
        self.ready = asyncio.Event()

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        logger.info("Notification delivery worker started")
        while not self.stop_event.is_set():
            try:
                batch = await self.client.claim(limit=CLAIM_BATCH_LIMIT)
            except httpx.HTTPError, NotificationProtocolError, ValidationError:
                self.ready.clear()
                logger.warning("Notification claim failed; retrying")
                await self._wait(self.failure_wait_seconds)
                continue

            if not self.ready.is_set():
                logger.info("Notification delivery worker is ready")
            self.ready.set()
            if not batch.items:
                await self._wait(self.idle_wait_seconds)
                continue

            for notification in batch.items:
                if self.stop_event.is_set():
                    return
                await self._deliver(notification)
        logger.info("Notification delivery worker stopped")

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _deliver(self, notification: ClaimedNotification) -> None:
        request = AcknowledgeRequest(
            lease_token=notification.lease_token,
            outcome="sent",
        )
        try:
            for page in render_notification(notification):
                await self.bot.send_message(
                    chat_id=notification.chat_id,
                    text=page,
                    parse_mode=ParseMode.HTML,
                )
        except TelegramRetryAfter as exc:
            if exc.retry_after > 86400:
                request = AcknowledgeRequest(
                    lease_token=notification.lease_token,
                    outcome="terminal",
                    error_code="telegram_retry_after_too_long",
                )
            else:
                request = AcknowledgeRequest(
                    lease_token=notification.lease_token,
                    outcome="retryable",
                    error_code="telegram_rate_limited",
                    retry_after_seconds=exc.retry_after,
                )
        except TelegramForbiddenError:
            request = AcknowledgeRequest(
                lease_token=notification.lease_token,
                outcome="terminal",
                error_code="telegram_forbidden",
            )
        except TelegramBadRequest as exc:
            code = (
                "telegram_chat_not_found"
                if "chat not found" in str(exc).casefold()
                else "telegram_bad_request"
            )
            request = AcknowledgeRequest(
                lease_token=notification.lease_token,
                outcome="terminal",
                error_code=code,
            )
        except TelegramUnauthorizedError:
            raise
        except TelegramNetworkError, TelegramServerError:
            request = AcknowledgeRequest(
                lease_token=notification.lease_token,
                outcome="unknown",
                error_code="telegram_send_unknown",
            )
        except TelegramAPIError:
            request = AcknowledgeRequest(
                lease_token=notification.lease_token,
                outcome="terminal",
                error_code="telegram_api_error",
            )

        try:
            await self.client.acknowledge(notification.id, request)
        except httpx.HTTPError, NotificationProtocolError, ValidationError:
            logger.warning(
                "Notification acknowledgement failed; lease will recover",
                extra={"notification_id": str(notification.id)},
            )
            return
        logger.info(
            "Notification attempt acknowledged",
            extra={
                "notification_id": str(notification.id),
                "outcome": request.outcome,
                "error_code": request.error_code,
            },
        )
