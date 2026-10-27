import logging
from typing import Literal

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from aiogram_dialog import DialogManager

logger = logging.getLogger(__name__)

_EXPECTED_DELETE_FAILURES = (
    "message to delete not found",
    "message can't be deleted for everyone",
)


async def delete_accepted_input_message(
    message: Message,
    manager: DialogManager,
    input_type: Literal["date", "time"],
) -> bool:
    try:
        await message.delete()
    except TelegramBadRequest as exc:
        description = str(exc).casefold()
        if not any(reason in description for reason in _EXPECTED_DELETE_FAILURES):
            raise

        logger.warning(
            "Could not delete accepted %s input (chat_id=%s, message_id=%s): %s",
            input_type,
            message.chat.id,
            message.message_id,
            exc,
        )
        notice = (
            f"{input_type.title()} accepted, but Telegram could not remove your message. "
            "Delete it manually if needed."
        )
        current = manager.dialog_data.get("error")
        manager.dialog_data["error"] = f"{current}\n{notice}" if current else notice
        return False
    return True


async def delete_accepted_date_message(
    message: Message, manager: DialogManager
) -> bool:
    return await delete_accepted_input_message(message, manager, "date")
