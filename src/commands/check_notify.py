from aiogram import Router
from aiogram.types import Message
from sqlalchemy.orm import Session

import messages
from config import config
from database import engine
from models import Reschedule, ScheduledLesson, Teacher

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
        breaks = {b.weekday: b for b in teacher.breaks}
        students = [s.id for s in teacher.students]

        wrong_time_events = []
        sls = session.query(ScheduledLesson).filter(ScheduledLesson.user_id.in_(students)).all()
        for sl in sls:
            wb = breaks.get(sl.weekday)
            if sl.weekday in weekends:
                wd = config.WEEKDAY_MAP_FULL[sl.weekday]
                text += f"Урок {wd}-{sl.start_time} у {sl.user.username_dog} стоит в выходной\n"
                wrong_time_events.append(sl)
            elif wb and sl.start_time >= wb.start_time and sl.start_time < wb.end_time:
                wd = config.WEEKDAY_MAP_FULL[sl.weekday]
                text += f"Урок {wd}-{sl.start_time} у {sl.user.username_dog} стоит в перерыве\n"
                wrong_time_events.append(sl)

        rss = session.query(Reschedule).filter(Reschedule.user_id.in_(students), Reschedule.date.is_not(None)).all()
        for rs in rss:
            rsw = rs.date.weekday()
            wb = breaks.get(rsw)
            if rsw in weekends:
                wd = config.WEEKDAY_MAP_FULL[rsw]
                text += f"Перенос {rs.date} {rs.st_str} у {rs.user.username_dog} стоит в выходной ({wd})\n"
                wrong_time_events.append(rs)
            elif rsw in breaks and rs.start_time >= breaks[rsw].start_time and rs.start_time < breaks[rsw].end_time:
                text += f"Перенос {rs.date} {rs.st_str} у {rs.user.username_dog} стоит в перерыве\n"
                wrong_time_events.append(rs)

        await message.answer(text)
