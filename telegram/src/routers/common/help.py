from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.keyboards import all_commands
from src.messages import replies
from src.service import UserService
from src.service.utils import telegram_checks

router: Router = Router()


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    """Handler receives messages with `/help` command."""
    message = telegram_checks(message)
    service = UserService(message)
    user = await service.get_user()
    if user is None:
        await message.answer(replies.HELP_MESSAGE, reply_markup=all_commands("guest"))
        return
    await message.answer(replies.HELP_MESSAGE, reply_markup=all_commands(user.role))
