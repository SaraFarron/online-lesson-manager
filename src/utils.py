from __future__ import annotations

from typing import Iterable

import aiohttp
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.base import getenv
from config.config import ADMINS


async def send_message(telegram_id: int, message: str) -> None:
    """Send a message to the user."""
    token = getenv("BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={telegram_id}&text={message}"
    async with aiohttp.ClientSession() as session, session.get(url) as resp:
        await resp.text()


async def notify_admins(message: str) -> None:
    """Send a message to all admins."""
    for admin in ADMINS:
        await send_message(admin, message)


def inline_keyboard(buttons: dict[str, str] | Iterable[tuple[str, str]]):
    """Create an inline keyboard."""
    builder = InlineKeyboardBuilder()
    if isinstance(buttons, dict):
        for callback_data, text in buttons.items():
            builder.button(text=text, callback_data=callback_data)
    else:
        for text, callback_data in buttons:
            builder.button(text=text, callback_data=callback_data)
    return builder
