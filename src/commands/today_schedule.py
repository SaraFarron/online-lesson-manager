from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import logs
from config.config import TIMEZONE
from database import engine
from help import Commands
from logger import log_func, logger
from models import Teacher, User
from utils import today_schedule_for_teacher, today_schedule_for_user

COMMAND = "today_schedule"
router: Router = Router()


class Messages:
    NOT_REGISTERED = "Вы не зарегистрированы. Пожалуйста воспользуйтесь командой /start"
    SCHEDULE_EMPTY = "На сегодня занятий нет"
    SCHEDULE = "Занятия на сегодня:\n"


def get_todays_schedule(date: datetime, telegram_id: int) -> list[dict[str, str]]:
    """Gets schedule string depending on the date and user status."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == telegram_id).first()
        if teacher:
            schedule = "\n".join([f"{s[0]}-{s[1]}: {s[2]}" for s in today_schedule_for_teacher(date, teacher)])
        else:
            schedule = "\n".join([f"{s[0]}-{s[1]}" for s in today_schedule_for_user(date, telegram_id)])
    if not schedule:
        return Messages.SCHEDULE_EMPTY
    return Messages.SCHEDULE + schedule


@router.message(Command(COMMAND))
@router.message(F.text == Commands.TODAY_SCHEDULE.value)
@log_func
async def today_schedule_handler(message: Message) -> None:
    """Handler returns today's schedule."""
    today = datetime.now(TIMEZONE)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            logger.info(logs.REQUEST_SCHEDULE, message.from_user.full_name)
            await message.answer(get_todays_schedule(today, user.telegram_id))
        else:
            logger.warn(logs.REQUEST_SCHEDULE_NO_USER, message.from_user.full_name)
            await message.answer(Messages.NOT_REGISTERED)
