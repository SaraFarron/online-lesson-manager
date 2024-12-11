from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from help import Commands
from logger import log_func
from models import Reschedule, ScheduledLesson, User
from routers.reschedule.config import FRL_START_CALLBACK, ORL_RS_CALLBACK, ORL_START_CALLBACK, router
from utils import inline_keyboard, this_week

COMMAND = "/reschedule"


class Messages:
    CHSE_LSN_1 = "Выберите занятие. Нельзя отменять занятия, до которых осталось меньше "
    CHSE_LSN_2 = f"{config.HRS_TO_CANCEL} часов" if config.HRS_TO_CANCEL > 1 else f"{config.HRS_TO_CANCEL} часа"
    CHSE_LSN_3 = " (такие предложены не будут)"
    CHOOSE_LESSON = CHSE_LSN_1 + CHSE_LSN_2 + CHSE_LSN_3
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
async def reschedule_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `/reschedule` command."""
    user_id = message.from_user.id # type: ignore  # noqa: PGH003
    if user_id in config.BANNED_USERS:
        return
    user = db.query(User).filter(User.telegram_id == user_id).first()
    if user:
        await state.update_data(user_id=user.id)
        lessons = (
            db.query(ScheduledLesson)
            .filter(ScheduledLesson.user_id == user.id)
            .order_by(ScheduledLesson.weekday, ScheduledLesson.start_time)
            .all()
        )
        now = datetime.now(config.TIMEZONE)
        reschedules = db.query(Reschedule).filter(Reschedule.user_id == user.id, Reschedule.date >= now.date()).all()
        if lessons or reschedules:
            weekdays = {d.weekday(): config.WEEKDAY_MAP_FULL[d.weekday()] for d in this_week()}
            buttons = [
                (
                    f"{weekdays[lesson.weekday]} {lesson.start_time}",
                    Callbacks.CHOOSE_CANCEL_TYPE + str(lesson.id),
                )
                for lesson in lessons
                if lesson.may_cancel(now)
            ] + [
                (
                    f"{rs!s}",
                    ORL_RS_CALLBACK + "rs:" + str(rs.id),
                )
                for rs in reschedules
                if rs.may_cancel(now)
            ]
            if not buttons:
                await message.answer(Messages.NO_LESSONS)
                return
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
    lesson_id = int(callback.data.split(":")[1]) # type: ignore  # noqa: PGH003
    await state.update_data(lesson=lesson_id)
    buttons = [
        (Messages.ONE_TYPE, ORL_START_CALLBACK),
        (Messages.DELETE_TYPE, FRL_START_CALLBACK),
    ]
    keyboard = inline_keyboard(buttons)
    keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await callback.message.answer(Messages.CANCEL_TYPE, reply_markup=keyboard.as_markup()) # type: ignore  # noqa: PGH003
