from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from commands.reschedule.config import FRL_START_CALLBACK, ORL_RS_CALLBACK, ORL_START_CALLBACK, router
from config import config
from database import engine
from help import Commands
from logger import log_func
from models import Reschedule, ScheduledLesson, User
from utils import inline_keyboard, this_week

COMMAND = "/reschedule"


class Messages:
    CHOOSE_LESSON = "Выберите занятие"
    NO_LESSONS = "Нет предстоящих занятий"
    CANCEL_TYPE = "Вы можете отменить/перенести занятие навсегда или только в какую-то дату"
    DELETE_TYPE = "Навсегда"
    ONE_TYPE = "Только на одну дату"
    NOT_REGISTERED = "Вы не зарегистрированы. Пожалуйста воспользуйтесь командой /start"


class Callbacks:
    CHOOSE_CANCEL_TYPE = "rl_choose_cancel_type:"
    CHOOSE_SL_DATE_FOREVER = "rl_choose_date_sl:forever"
    CHOOSE_SL_DATE_ONCE = "rl_choose_date_sl:once"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.RESCHEDULE.value)
@log_func
async def reschedule_lesson_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/reschedule` command."""
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            await state.update_data(user_id=user.id)
            lessons = (
                session.query(ScheduledLesson)
                .filter(ScheduledLesson.user_id == user.id)
                .order_by(ScheduledLesson.weekday, ScheduledLesson.start_time)
                .all()
            )
            now = datetime.now(config.TIMEZONE)
            reschedules = session.query(Reschedule).filter(Reschedule.user_id == user.id, Reschedule.date >= now).all()
            if lessons or reschedules:
                weekdays = {d.weekday(): config.WEEKDAY_MAP_FULL[d.weekday()] for d in this_week()}
                buttons = [
                    (
                        f"{weekdays[lesson.weekday]} {lesson.start_time}",
                        Callbacks.CHOOSE_CANCEL_TYPE + str(lesson.id),
                    )
                    for lesson in lessons
                ] + [(f"{rs!s}", ORL_RS_CALLBACK + "rs:" + str(rs.id)) for rs in reschedules]
                keyboard = inline_keyboard(buttons)
                keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
                await message.answer(Messages.CHOOSE_LESSON, reply_markup=keyboard.as_markup())
            else:
                await message.answer(Messages.NO_LESSONS)
        else:
            await message.answer(Messages.NOT_REGISTERED)


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_CANCEL_TYPE))
@log_func
async def reschedule_lesson_choose_cancel_type_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    lesson_id = int(callback.data.split(":")[1])
    await state.update_data(lesson=lesson_id)
    buttons = [
        (Messages.ONE_TYPE, ORL_START_CALLBACK),
        (Messages.DELETE_TYPE, FRL_START_CALLBACK),
    ]
    keyboard = inline_keyboard(buttons)
    keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await callback.message.answer(Messages.CANCEL_TYPE, reply_markup=keyboard.as_markup())
