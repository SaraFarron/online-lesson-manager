import logging
import re

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, ReplyKeyboardRemove

logger = logging.getLogger(__name__)

_COMMAND_PATTERN = re.compile(r"^/\w+(?:@\w+)?(?:\s|$)")
_KEYBOARD_REMOVAL_TEXT = "…"


def is_private_command(message: Message) -> bool:
    return (
        message.chat.type == "private"
        and message.text is not None
        and _COMMAND_PATTERN.match(message.text) is not None
    )


async def delete_command_message(message: Message) -> bool:
    try:
        await message.delete()
    except TelegramAPIError as exc:
        logger.warning(
            "Could not delete command message (message_id=%s): %s",
            message.message_id,
            exc,
        )
        return False
    return True


async def remove_stale_reply_keyboard(message: Message) -> None:
    # A reply keyboard can only be dismissed by a new message, so send one and
    # remove it immediately; failures must never block the caller.
    try:
        notice = await message.answer(
            _KEYBOARD_REMOVAL_TEXT, reply_markup=ReplyKeyboardRemove()
        )
    except TelegramAPIError as exc:
        logger.warning("Could not remove the reply keyboard: %s", exc)
        return
    await delete_command_message(notice)
