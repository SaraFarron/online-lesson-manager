from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from db.database import engine
from logger import logger


class DatabaseMiddleware(BaseMiddleware):
    """Throws a session class to handler."""

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Calls every update."""
        with Session(bind=engine) as session:
            data["db"] = session
            return await handler(event, data)


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
