from aiogram import Router
from aiogram.types import Message
from sqlalchemy.orm import Session

import messages
from config import config
from database import engine
from models import Reschedule, ScheduledLesson, Teacher
from utils import get_weekday_dates

router: Router = Router()


async def check_notify_handler(message: Message) -> None:
    """Handler receives messages with `/check_notify` command."""
    with Session(engine) as session:
        teacher: Teacher | None = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if teacher is None:
            await message.answer(messages.PERMISSION_DENIED)
            return
        text = ""
        weekends = [we.weekday for we in teacher.weekends]
        students = [s.id for s in teacher.students]
        lessons = (
            session.query(ScheduledLesson)
            .filter(ScheduledLesson.weekday.in_(weekends), ScheduledLesson.user_id.in_(students))
            .all()
        )
        for lesson in lessons:
            wd = config.WEEKDAY_MAP_FULL[lesson.weekday]
            text += f"Урок {lesson.start_time} у {lesson.user.username_dog} стоит в выходной ({wd})\n"
        text += "\n"
        weekend_dates = []
        for we in weekends:
            weekend_dates.extend(list(get_weekday_dates(1, we)))
        reschedules = session.query(Reschedule).filter(Reschedule.date.in_(weekend_dates)).all()
        for rs in reschedules:
            text += f"Перенос {rs.date} {rs.st_str} у {rs.user.username_dog} стоит в выходной ({wd})\n"

        for b in teacher.breaks:
            lessons = (
                session.query(ScheduledLesson)
                .filter(ScheduledLesson.weekday == b.weekday, ScheduledLesson.user_id.in_(students))
                .all()
            )
            for lesson in lessons:
                if lesson.start_time >= b.start_time and lesson.start_time < b.end_time:
                    wd = config.WEEKDAY_MAP_FULL[lesson.weekday]
                    text += f"Урок {wd}-{lesson.start_time} у {lesson.user.username_dog} стоит в перерыве\n"

            reschedules = session.query(Reschedule).filter(Reschedule.date == b.date).all()  # TODO

        await message.answer(text)
