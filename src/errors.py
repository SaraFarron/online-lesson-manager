from aiogram import Dispatcher, F
from aiogram.filters.exception import ExceptionTypeFilter
from aiogram.types.error_event import ErrorEvent
from aiogram.types.message import Message

from config.config import ADMINS
from messages import errors as err_msgs
from utils import send_message


class PermissionDeniedError(Exception):
    pass


class AiogramTelegramError(Exception):
    pass


def add_errors(dp: Dispatcher):
    """Adds all errors to dispatcher."""

    @dp.errors(ExceptionTypeFilter(PermissionDeniedError), F.update.message.as_("message"))
    async def permission_denied(event: ErrorEvent, message: Message) -> None:
        await message.answer(err_msgs.PERMISSION_DENIED)

    @dp.errors(ExceptionTypeFilter(AiogramTelegramError), F.update.message.as_("message"))
    async def aiogram_telegram_error(event: ErrorEvent, message: Message) -> None:
        await send_message(ADMINS["sara_farron"], f"error occured: {event.exception}\nmessage: {message}")
        await message.answer(err_msgs.TELEGRAM_ERROR_OCCURED)

    @dp.errors(ExceptionTypeFilter(Exception), F.update.message.as_("message"))
    async def value_error(event: ErrorEvent, message: Message) -> None:
        await message.answer(err_msgs.UNKNOWN)

    return dp
