from aiogram import Dispatcher, F
from aiogram.filters.exception import ExceptionTypeFilter
from aiogram.types.error_event import ErrorEvent
from aiogram.types.message import Message

from src.core.logger import logger
from src.interface.messages import errors as err_msgs


def add_errors(dp: Dispatcher):
    """Adds all errors to dispatcher."""

    @dp.errors(ExceptionTypeFilter(Exception), F.update.message.as_("message"))
    async def value_error(event: ErrorEvent, message: Message) -> None:
        err_data = event.exception.args
        if not err_data:
            logger.error(err_data)
            logger.exception(event.exception)
        elif err_data[0] == "message":
            await message.answer(err_data[1])
            logger.error(err_data[2])
        else:
            await message.answer(err_msgs.UNKNOWN)
            logger.error(err_data)
            logger.exception(event.exception)

    return dp
