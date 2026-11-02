import asyncio

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import TelegramMethod
from aiogram.methods.base import Response, TelegramType

_OUTBOUND_METHODS = frozenset(
    {
        "sendMessage",
        "sendDocument",
        "sendPhoto",
        "sendVideo",
        "sendAudio",
        "sendVoice",
        "sendAnimation",
        "sendSticker",
        "sendLocation",
        "sendVenue",
        "sendContact",
        "sendPoll",
        "sendDice",
        "editMessageText",
        "editMessageCaption",
        "editMessageMedia",
        "editMessageReplyMarkup",
        "copyMessage",
        "forwardMessage",
    }
)


class OutboundRateLimiter(BaseRequestMiddleware):
    def __init__(self, requests_per_second: int = 25) -> None:
        if requests_per_second < 1:
            raise ValueError("Outbound request rate must be positive")
        self.minimum_interval = 1 / requests_per_second
        self._lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if method.__api_method__ not in _OUTBOUND_METHODS:
            return await make_request(bot, method)
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next_request_at - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = loop.time() + self.minimum_interval
            return await make_request(bot, method)
