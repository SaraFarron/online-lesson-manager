from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import logs, messages
from config.config import TIMEZONE
from help import Commands
from logger import log_func, logger
from middlewares import DatabaseMiddleware
from repositories import UserRepo
from service import Schedule

COMMAND = "today_schedule"
router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message(Command(COMMAND))
@router.message(F.text == Commands.TODAY_SCHEDULE.value)
@log_func
async def today_schedule_handler(message: Message, db: Session) -> None:
    """Handler returns today's schedule."""
    t_user = message.from_user
    if not t_user:
        msg = "NO TELEGRAM USER"
        raise ValueError(msg)
    user = UserRepo(db).get_by_telegram_id(t_user.id)
    if user:
        logger.info(logs.REQUEST_SCHEDULE, t_user.full_name)
        schedule = Schedule(db)
        await message.answer(schedule.lessons_day_message(user, datetime.now(TIMEZONE)))
    else:
        logger.warning(logs.REQUEST_SCHEDULE_NO_USER, t_user.full_name)
        await message.answer(messages.NOT_REGISTERED)
