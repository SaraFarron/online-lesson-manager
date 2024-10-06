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
from utils import StudentSchedule, TeacherSchedule

COMMAND = "today_schedule"
router: Router = Router()


class Messages:
    NOT_REGISTERED = "Вы не зарегистрированы. Пожалуйста воспользуйтесь командой /start"
    SCHEDULE_EMPTY = "На сегодня занятий нет"
    SCHEDULE = "Занятия на сегодня:\n"


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
            if session.query(Teacher).filter(Teacher.telegram_id == user.telegram_id).first():
                answer = "\n".join(TeacherSchedule(user).schedule_day(today))
            else:
                answer = "\n".join(StudentSchedule(user).schedule_day(today))
            await message.answer(Messages.SCHEDULE + answer if answer else Messages.SCHEDULE_EMPTY)
        else:
            logger.warning(logs.REQUEST_SCHEDULE_NO_USER, message.from_user.full_name)
            await message.answer(Messages.NOT_REGISTERED)
