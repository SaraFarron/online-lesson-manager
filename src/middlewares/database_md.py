from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import engine


class DatabaseMiddleware(BaseMiddleware):
    """Throws a session class to handler."""

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Calls every update."""
        async with AsyncSession(bind=engine) as session:
            data["db"] = session
            return await handler(event, data)
