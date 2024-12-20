from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from config.config import TIMEZONE
from errors import AiogramTelegramError
from help import Commands
from messages import replies
from middlewares import DatabaseMiddleware
from repositories import UserRepo
from service import Schedule

COMMAND = "today_schedule"
router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message(Command(COMMAND))
@router.message(F.text == Commands.TODAY_SCHEDULE.value)
async def today_schedule_handler(message: Message, db: Session) -> None:
    """Handler returns today's schedule."""
    t_user = message.from_user
    if not t_user:
        raise AiogramTelegramError
    user = UserRepo(db).get_by_telegram_id(t_user.id)
    if user:
        schedule = Schedule(db)
        await message.answer(schedule.lessons_day_message(user, datetime.now(TIMEZONE).date()))
    else:
        await message.answer(replies.NOT_REGISTERED)
