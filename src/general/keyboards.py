from __future__ import annotations

from aiogram.utils.keyboard import ReplyKeyboardBuilder

from config import help
from config.config import ADMINS


def all_commands_keyboard(user_id: int):
    """Create a keyboard with available commands."""
    builder = ReplyKeyboardBuilder()
    builder.button(text=f"{help.START}")
    builder.button(text=f"{help.HELP}")
    builder.button(text=f"{help.ADD_LESSON}")
    builder.button(text=f"{help.REMOVE_LESSON}")
    builder.button(text=f"{help.GET_SCHEDULE}")
    builder.button(text=f"{help.GET_SCHEDULE_WEEK}")
    builder.button(text=f"{help.CANCEL}")
    if user_id in ADMINS:
        builder.button(text=help.EDIT_WORKING_HOURS)
        builder.button(text=help.ADMIN_GROUP)
    builder.adjust(2, repeat=True)
    return builder.as_markup()
