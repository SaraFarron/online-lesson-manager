from aiogram.types import CallbackQuery
from aiogram.types.message import Message

from errors import AiogramTelegramError

MAX_HOUR = 23


def telegram_checks(event: Message | CallbackQuery):
    if isinstance(event, Message):
        if not event.from_user:
            raise AiogramTelegramError
        return event
    if not isinstance(event.message, Message):
        raise AiogramTelegramError
    return event.message
