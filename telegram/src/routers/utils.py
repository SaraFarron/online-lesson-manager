from aiogram.types import CallbackQuery, Message

from src.messages import replies
from src.service import UserService


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
    service = UserService(message, event.from_user.id)  # ty:ignore[possibly-missing-attribute]
    user = await service.get_user()
    if user is None:
        await message.answer(replies.PERMISSION_DENIED)
        return None, message
    return user, message
