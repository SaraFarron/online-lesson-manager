from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import logs
from config.config import TIMEZONE, WEEKDAY_MAP
from database import engine
from help import Commands
from logger import log_func, logger
from models import Lesson, Reschedule, ScheduledLesson, Teacher, User

COMMAND = "today_schedule"
router: Router = Router()


class Messages:
    NOT_REGISTERED = "Вы не зарегистрированы. Пожалуйста воспользуйтесь командой /start"
    SCHEDULE_EMPTY = "На сегодня занятий нет"
    SCHEDULE = "Занятия на сегодня:\n"


def today_schedule_for_user(date: datetime, user_id: int):
    """Gets today's schedule for the user."""
    schedule = []
    weekday = WEEKDAY_MAP[date.weekday()]
    with Session(engine) as session:
        # Get regular lessons
        lessons = session.query(Lesson).filter(Lesson.date == date.date(), Lesson.user_id == user_id).all()
        schedule = [(lesson.time, lesson.end_time) for lesson in lessons]

        # Get reschedules
        reschedules = (
            session.query(Reschedule).filter(Reschedule.date == date.date(), Reschedule.user_id == user_id).all()
        )
        for reschedule in reschedules:
            schedule.append((reschedule.start_time, reschedule.end_time))
        canceled_sls = [rs.source for rs in reschedules]

        # Get scheduled lessons
        scheduled_lessons = (
            session.query(ScheduledLesson)
            .filter(ScheduledLesson.weekday == weekday, ScheduledLesson.user_id == user_id)
            .all()
        )
        for scheduled_lesson in scheduled_lessons:
            if scheduled_lesson not in canceled_sls:
                schedule.append((scheduled_lesson.start_time, scheduled_lesson.end_time))
    schedule.sort(key=lambda x: x[0])
    return schedule


def today_schedule_for_teacher(date: datetime, teacher: Teacher):
    """Gets today's schedule for the teacher."""
    schedule = []
    weekday = WEEKDAY_MAP[date.weekday()]
    with Session(engine) as session:
        students = [s.id for s in teacher.students]
        # Get regular lessons
        lessons = session.query(Lesson).filter(Lesson.date == date.date(), Lesson.user_id.in_(students)).all()
        schedule = [(lesson.time, lesson.end_time, lesson.user.name) for lesson in lessons]

        # Get reschedules
        reschedules = (
            session.query(Reschedule).filter(Reschedule.date == date.date(), Reschedule.user_id.in_(students)).all()
        )
        for reschedule in reschedules:
            schedule.append((reschedule.start_time, reschedule.end_time, reschedule.user.name))
        canceled_sls = [rs.source for rs in reschedules]

        # Get scheduled lessons
        scheduled_lessons = (
            session.query(ScheduledLesson)
            .filter(ScheduledLesson.weekday == weekday, ScheduledLesson.user_id.in_(students))
            .all()
        )
        for scheduled_lesson in scheduled_lessons:
            if scheduled_lesson not in canceled_sls:
                schedule.append((scheduled_lesson.start_time, scheduled_lesson.end_time, scheduled_lesson.user.name))
    schedule.sort(key=lambda x: x[0])
    return schedule


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
