from aiogram import Dispatcher, F
from aiogram.filters.exception import ExceptionTypeFilter
from aiogram.types.error_event import ErrorEvent
from aiogram.types.message import Message

import messages


class PermissionDeniedError(Exception):
    pass


def add_errors(dp: Dispatcher):
    """Adds all errors to dispatcher."""

    @dp.errors(ExceptionTypeFilter(PermissionDeniedError), F.update.message.as_("message"))
    async def permission_denied(event: ErrorEvent, message: Message) -> None:
        await message.answer(messages.PERMISSION_DENIED)

    @dp.errors(ExceptionTypeFilter(ValueError), F.update.message.as_("message"))
    async def value_error(event: ErrorEvent, message: Message) -> None:
        await message.answer("error occured")

    return dp
