from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from src.core.middlewares import DatabaseMiddleware
from src.db.repositories import EventHistoryRepo
from src.interface.keyboards import Keyboards
from src.interface.messages import replies
from src.service.services import UserService

COMMAND = "help"
router: Router = Router()




@router.message(Command(COMMAND))
async def help_handler(message: Message, db: Session) -> None:
    """Handler receives messages with `/help` command."""
    message, user = UserService(db).check_user(message)
    await message.answer(replies.HELP_MESSAGE, reply_markup=Keyboards.all_commands(user.role))
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(username, "help", "help", "")
