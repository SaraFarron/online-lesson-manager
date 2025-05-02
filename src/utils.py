from aiogram.types import CallbackQuery
from aiogram.types.message import Message

MAX_HOUR = 23


def telegram_checks(event: Message | CallbackQuery):
    if isinstance(event, Message):
        if not event.from_user:
            raise Exception("Ошибка на стороне telegram", "event.from_user is False")
        return event
    if not isinstance(event.message, Message):
        raise Exception("Ошибка на стороне telegram", "event.message is not Message")
    return event.message
