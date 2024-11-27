from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from config.config import ADMINS
from config.messages import HELP_MESSAGE
from help import AdminCommands, Commands
from logger import log_func

COMMAND = "help"
router: Router = Router()


def all_commands_keyboard(user_id: int):
    """Create a keyboard with available commands."""
    builder = ReplyKeyboardBuilder()
    for command in Commands:
        builder.button(text=command.value)
    if user_id in ADMINS:
        for command in AdminCommands:
            builder.button(text=command.value)
    builder.adjust(2, repeat=True)
    return builder.as_markup()


@router.message(Command(COMMAND))
@log_func
async def help_handler(message: Message) -> None:
    """Handler receives messages with `/help` command."""
    await message.answer(HELP_MESSAGE, reply_markup=all_commands_keyboard(message.from_user.id)) # type: ignore  # noqa: PGH003
