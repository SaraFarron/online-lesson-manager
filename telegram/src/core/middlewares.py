from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from src.core.logger import logger


class LoggingMiddleware(BaseMiddleware):
    """Logs every update."""

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Calls every update."""
        if event.from_user:
            user_input = event.text if isinstance(event, Message) else event.data
            log = f"User {event.from_user.full_name}({event.from_user.id}) sent {user_input}"
            logger.info(log)
        else:
            logger.warning(f"Called {handler.__name__} without user")
        return await handler(event, data)
