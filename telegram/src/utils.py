from datetime import date, datetime, time
from os import getenv

import aiohttp
from aiogram.types import CallbackQuery, Message

from src.core.config import SHORT_DATE_FMT
from src.messages import replies
from src.service import UserService

MAX_HOUR = 23


def telegram_checks(event: Message | CallbackQuery):
    """Checks if the event is a Message or a CallbackQuery and returns the Message object."""
    if isinstance(event, Message):
        if not event.from_user:
            raise Exception("message", "Ошибка на стороне telegram", "event.from_user is False")
        return event
    if not isinstance(event.message, Message):
        raise Exception("message", "Ошибка на стороне telegram", "event.message is not Message")
    return event.message


async def student_permission(event: Message | CallbackQuery):
    """
    Checks if the user has permission to perform the action.
    If the user is not registered, sends a permission denied message and returns None.
    """
    message = telegram_checks(event)
    service = UserService(message)
    user = await service.get_user()
    if user is None:
        await message.answer(replies.PERMISSION_DENIED)
        return None, message
    return user, message


def parse_date(text: str, in_future: bool = False) -> date | None:
    for fmt in (
        SHORT_DATE_FMT,
        SHORT_DATE_FMT.replace(".", " "),
        SHORT_DATE_FMT.replace(".", "-"),
        "%Y-%m-%d",
        "%Y %m %d",
        "%Y.%m.%d",
    ):
        try:
            date = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        now = datetime.now().date()
        if date.year < now.year:
            if in_future and date.replace(year=now.year) < now:
                return date.replace(year=now.year + 1)
            return date.replace(year=now.year)
        return date
    return None


def parse_time(text: str):
    for fmt in ("%H:%M", "%H %M"):
        try:
            time = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return time
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
        response = await resp.text()
    if '"ok":false' in response:
        print(f"tg_id:{telegram_id}\nmessage:{message}\nresponse:{response}")
