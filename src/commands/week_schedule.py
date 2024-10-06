from __future__ import annotations

from datetime import datetime

from aiogram import F, Router, html
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import logs
from config.config import WEEKDAY_MAP_FULL
from database import engine
from help import Commands
from logger import log_func, logger
from models import Teacher, User
from utils import StudentSchedule, TeacherSchedule, this_week

COMMAND = "week_schedule"
router: Router = Router()


class Messages:
    NOT_REGISTERED = "Вы не зарегистрированы. Пожалуйста воспользуйтесь командой /start"
    SCHEDULE_EMPTY = "Занятий нет"


def get_todays_schedule(date: datetime, user: User):
    """Gets schedule string depending on the date and user status."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == user.telegram_id).first()
        if teacher:
            schedule = "\n".join(TeacherSchedule(user).schedule_day(date))
        else:
            schedule = "\n".join(StudentSchedule(user).schedule_day(date))
    if not schedule:
        return Messages.SCHEDULE_EMPTY
    return schedule


def get_week_schedule(user: User):
    """Get lessons for the current week."""
    return [(get_todays_schedule(current_day, user), current_day) for current_day in this_week()]


@router.message(Command(COMMAND))
@router.message(F.text == Commands.WEEK_SCHEDULE.value)
@log_func
async def week_schedule_handler(message: Message) -> None:
    """Handler returns today's schedule."""
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            logger.info(logs.REQUEST_SCHEDULE, message.from_user.full_name)
            week_schedule = "\n\n".join(
                html.bold(WEEKDAY_MAP_FULL[date.weekday()]) + f" {date.strftime('%d.%m.%Y')}:\n" + day_schedule
                for day_schedule, date in get_week_schedule(user)
                if day_schedule != Messages.SCHEDULE_EMPTY
            )
            if not week_schedule:
                week_schedule = Messages.SCHEDULE_EMPTY
            await message.answer(week_schedule)
        else:
            logger.warning(logs.REQUEST_SCHEDULE_NO_USER, message.from_user.full_name)
            await message.answer(Messages.NOT_REGISTERED)
