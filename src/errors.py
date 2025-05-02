from aiogram import Dispatcher, F
from aiogram.filters.exception import ExceptionTypeFilter
from aiogram.types.error_event import ErrorEvent
from aiogram.types.message import Message

from logger import logger
from messages import errors as err_msgs


def add_errors(dp: Dispatcher):
    """Adds all errors to dispatcher."""

    @dp.errors(ExceptionTypeFilter(Exception), F.update.message.as_("message"))
    async def value_error(event: ErrorEvent, message: Message) -> None:
        err_data = event.exception.args
        if len(err_data) > 0 and err_data[0]:
            await message.answer(err_data[0])
        else:
            await message.answer(err_msgs.UNKNOWN)
        if len(err_data) > 1 and err_data[1]:
            logger.error(err_data[1])
        else:
            logger.error(err_data)


    return dp
