from __future__ import annotations

from datetime import datetime, timedelta

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

COMMAND = "week_schedule"
router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message(Command(COMMAND))
@router.message(F.text == Commands.WEEK_SCHEDULE.value)
async def week_schedule_handler(message: Message, db: Session) -> None:
    """Handler returns today's schedule."""
    t_user = message.from_user
    if not t_user:
        raise AiogramTelegramError
    user = UserRepo(db).get_by_telegram_id(t_user.id)
    if user:
        schedule = Schedule(db)
        today = datetime.now(TIMEZONE)
        start_of_week = today - timedelta(days=today.weekday())
        await message.answer(schedule.lessons_week_message(user, start_of_week.date()))
    else:
        await message.answer(replies.NOT_REGISTERED)
