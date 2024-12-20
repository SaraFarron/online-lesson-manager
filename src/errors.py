from aiogram import Dispatcher, F
from aiogram.filters.exception import ExceptionTypeFilter
from aiogram.types.error_event import ErrorEvent
from aiogram.types.message import Message

from config.config import ADMINS
from logger import logger
from messages import errors as err_msgs
from utils import send_message


class NoTextMessageError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class AiogramTelegramError(Exception):
    pass


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
