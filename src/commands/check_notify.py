from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

import messages
from config import config
from database import engine
from help import AdminCommands
from logger import log_func
from models import Reschedule, ScheduledLesson, Teacher
from utils import inline_keyboard, send_message

router: Router = Router()

COMMAND = "/check_notify"


class Callbacks:
    CHECK_NOTIFY = "check_notify:"


@router.message(Command(COMMAND))
@router.message(F.text == AdminCommands.CHECK_SCHEDULE.value)
@log_func
async def check_notify_handler(message: Message, state: FSMContext) -> None:
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
                text += f"{sl!s} у {sl.user.username_dog} стоит в выходной\n"
                wrong_time_events.append(sl)
            elif wb and sl.start_time >= wb.start_time and sl.start_time < wb.end_time:
                wd = config.WEEKDAY_MAP_FULL[sl.weekday]
                text += f"{sl!s} у {sl.user.username_dog} стоит в перерыве\n"
                wrong_time_events.append(sl)

        rss = session.query(Reschedule).filter(Reschedule.user_id.in_(students), Reschedule.date.is_not(None)).all()
        for rs in rss:
            if rs.date < datetime.now(config.TIMEZONE).date():
                continue
            rsw = rs.date.weekday()
            wb = breaks.get(rsw)
            if rsw in weekends:
                wd = config.WEEKDAY_MAP_FULL[rsw]
                text += f"{rs!s} у {rs.user.username_dog} стоит в выходной ({wd})\n"
                wrong_time_events.append(rs)
            elif rsw in breaks and rs.start_time >= breaks[rsw].start_time and rs.start_time < breaks[rsw].end_time:
                text += f"{rs!s} у {rs.user.username_dog} стоит в перерыве\n"
                wrong_time_events.append(rs)

        await state.update_data(wrong_time_events=wrong_time_events)

        keyboard = inline_keyboard(
            [
                ("Отправить сообщения", Callbacks.CHECK_NOTIFY + "send"),
                ("Отмена", Callbacks.CHECK_NOTIFY + "cancel"),
            ],
        )
        await message.answer(text, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHECK_NOTIFY))
@log_func
async def check_notify_finish(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `check_notify` callback."""
    if callback.data == Callbacks.CHECK_NOTIFY + "send":
        state_data = await state.get_data()
        with Session(engine) as session:
            for we in state_data["wrong_time_events"]:
                if isinstance(we, ScheduledLesson):
                    sl: ScheduledLesson | None = session.query(ScheduledLesson).get(we.id)
                    if not sl:
                        await callback.message.answer(
                            f"Ошибка при отправке уведомления об уроке {we.weekday_full_str}-{we.start_time}",
                        )
                        continue
                    await send_message(sl.user.telegram_id, messages.OUT_OF_WT % str(sl))
                elif isinstance(we, Reschedule):
                    rs: Reschedule | None = session.query(Reschedule).get(we.id)
                    if not rs:
                        await callback.message.answer(
                            f"Ошибка при отправке уведомления о переносе {we.date} в {we.st_str}",
                        )
                        continue
                    await send_message(rs.user.telegram_id, messages.OUT_OF_WT % str(rs))
                else:
                    continue
        await callback.message.answer("Уведомления отправлены")
    else:
        await state.clear()
        await callback.message.answer("Отменено")
