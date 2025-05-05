from datetime import datetime, time

import aiohttp
from aiogram.types import CallbackQuery
from aiogram.types.message import Message
from aiogram.fsm.state import State

from src.core.base import getenv

MAX_HOUR = 23


class RouterConf:
    scene = ""
    command = "/" + scene


def telegram_checks(event: Message | CallbackQuery):
    if isinstance(event, Message):
        if not event.from_user:
            raise Exception("message", "Ошибка на стороне telegram", "event.from_user is False")
        return event
    if not isinstance(event.message, Message):
        raise Exception("message", "Ошибка на стороне telegram", "event.message is not Message")
    return event.message


def parse_date(text: str):
    for fmt in ("%Y-%m-%d", "%Y %m %d", "%Y.%m.%d"):
        try:
            date = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return date
    return None


def get_callback_arg(callback_data: str, callback: str):
    return callback_data.replace(callback, "")


def calc_end_time(time: time):
    """Calculate end time."""
    return time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0)


async def send_message(telegram_id: int, message: str) -> None:
    """Send a message to the user."""
    token = getenv("BOT_TOKEN")
    message = message.replace("\n", "%0A")
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={telegram_id}&text={message}&parse_mode=HTML"
    async with aiohttp.ClientSession() as session, session.get(url) as resp:
        await resp.text()
