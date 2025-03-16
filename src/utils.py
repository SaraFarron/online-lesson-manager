from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, time, timedelta

import aiohttp
from aiogram import Dispatcher, F
from aiogram.filters.exception import ExceptionTypeFilter
from aiogram.types import CallbackQuery
from aiogram.types.error_event import ErrorEvent
from aiogram.types.message import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import config
from config.base import getenv
from config.config import ADMINS
from config.config import TIMEZONE
from errors import AiogramTelegramError, PermissionDeniedError, NoTextMessageError
from logger import logger
from messages import errors as err_msgs

MAX_HOUR = 23


def telegram_checks(event: Message | CallbackQuery):
    if isinstance(event, Message):
        if not event.from_user:
            raise AiogramTelegramError
        return event
    else:
        if not isinstance(event.message, Message):
            raise AiogramTelegramError
        return event.message


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


async def notify_admins(message: str) -> None:
    """Send a message to all admins."""
    for tg_id in ADMINS.values():
        await send_message(tg_id, message)


def callback_buttons(objs: list, callback_data: str):
    return [
        (obj, callback_data + str(obj)) for obj in objs
    ]


def this_week():
    """Get dates for the current week."""
    today = datetime.now(TIMEZONE)
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=7)
    return [start_of_week + timedelta(n) for n in range(int((end_of_week - start_of_week).days))]


def daterange(start: datetime, end: datetime, step: int = 1) -> Iterable[datetime]:
    """Get dates between two dates."""
    date_range = []
    current_date = start

    while current_date <= end:
        date_range.append(current_date)
        current_date += timedelta(days=step)

    return date_range


def add_errors(dp: Dispatcher):
    """Adds all errors to dispatcher."""

    @dp.errors(ExceptionTypeFilter(PermissionDeniedError), F.update.message.as_("message"))
    async def permission_denied(event: ErrorEvent, message: Message) -> None:  # noqa: ARG001
        await message.answer(err_msgs.PERMISSION_DENIED)

    @dp.errors(ExceptionTypeFilter(AiogramTelegramError), F.update.message.as_("message"))
    async def aiogram_telegram_error(event: ErrorEvent, message: Message) -> None:
        await send_message(ADMINS["sara_farron"], f"error occured: {event.exception}\nmessage: {message}")
        await message.answer(err_msgs.TELEGRAM_ERROR_OCCURED)

    @dp.errors(ExceptionTypeFilter(NoTextMessageError), F.update.message.as_("message"))
    async def no_text_message_error(event: ErrorEvent, message: Message) -> None:  # noqa: ARG001
        await message.answer(err_msgs.NOT_TEXT_MESSAGE)

    @dp.errors(ExceptionTypeFilter(Exception), F.update.message.as_("message"))
    async def value_error(event: ErrorEvent, message: Message) -> None:
        logger.exception(event.exception)
        await message.answer(err_msgs.UNKNOWN)

    return dp

