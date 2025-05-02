from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from src.keyboards import Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import EventHistory
from src.repositories import UserRepo
from src.utils import telegram_checks

COMMAND = "help"
router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message(Command(COMMAND))
async def help_handler(message: Message, db: Session) -> None:
    """Handler receives messages with `/help` command."""
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id)
    log = EventHistory(author=user.username, scene="help", event_type="help", event_value="")
    db.add(log)
    db.commit()
    await message.answer(replies.HELP_MESSAGE, reply_markup=Keyboards.all_commands(user.role))
