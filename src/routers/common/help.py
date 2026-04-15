from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from src.keyboards import Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.repositories import EventHistoryRepo, UserRepo
from src.utils import telegram_checks

COMMAND = "help"
router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message()
async def thanks_for_using_bot_handler(message: Message) -> None:
    """Handler receives messages with thanks for using the bot."""
    await message.answer(replies.THANKS_FOR_USING_BOT)


# @router.message(Command(COMMAND))
async def help_handler(message: Message, db: Session) -> None:
    """Handler receives messages with `/help` command."""
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id)
    if user is None:
        raise Exception(
            "message", "У вас нет прав на эту команду", "permission denied user is None"
        )
    await message.answer(
        replies.HELP_MESSAGE, reply_markup=Keyboards.all_commands(user.role)
    )
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(username, "help", "help", "")
