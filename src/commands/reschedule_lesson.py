from models import Reschedule, Weekend
from __future__ import annotations

from datetime import datetime
from typing import Literal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from database import engine
from help import Commands
from logger import log_func, logger
from models import RestrictedTime, ScheduledLesson, User
from utils import inline_keyboard, this_week

COMMAND = "/reschedule"

router = Router()


class Messages:
    CHOOSE_LESSON = "Выберите занятие"
    NO_LESSONS = "Нет предстоящих занятий"
    CONFRIM = "Вы можете назначить новое время, чтобы перенести урок"
    CHOOSE_DATE = "Введите дату в формате ДД-ММ-ГГГГ, нельзя выбрать %s"
    CHOOSE_TIME = "Выберите время"
    LESSON_ADDED = "Урок добавлен"
    NOT_REGISTERED = "Вы не зарегистрированы. Пожалуйста воспользуйтесь командой /start"


class Callbacks:
    CHOOSE_SL = "reschesule_lesson_choose_sl:"
    CONFIRM = "reschesule_lesson_confirm:"
    CHOOSE_DATE = "reschesule_lesson_choose_date:"
    CHOOSE_TIME = "reschesule_lesson_choose_time:"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.RESCHEDULE)
@log_func
async def reschedule_lesson_handler(message: Message) -> None:
    """Handler receives messages with `/reschedule` command."""
    today = datetime.now(config.TIMEZONE)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            lessons = session.query(ScheduledLesson).filter(
                ScheduledLesson.user_id == user.id,
                ScheduledLesson.weekday >= today.weekday(),
                ScheduledLesson.start_time >= today.time(),
            ).all()
            if lessons:
                weekdays = {d.weekday(): d for d in this_week()}
                buttons = [(weekdays[lesson.weekday], Callbacks.CHOOSE_SL + str(lesson.id)) for lesson in lessons]
                await message.answer(Messages.CHOOSE_LESSON, reply_markup=inline_keyboard(buttons).as_markup())
            else:
                await message.answer(Messages.NO_LESSONS)
        else:
            await message.answer(Messages.NOT_REGISTERED)


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_SL))
@log_func
async def reschesule_lesson_choose_sl_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    lesson_id = int(callback.data.split(":")[1])
    with Session(engine) as session:
        lesson: ScheduledLesson = session.query(ScheduledLesson).get(lesson_id)
        if lesson:
            await state.update_data(lesson=lesson_id, user_id=lesson.user_id)
            keyboard = inline_keyboard([
                ("Отменить урок", Callbacks.CONFIRM),
                ("Перенести на новую дату", Callbacks.CHOOSE_DATE),
            ]).as_markup()
            await callback.message.answer(Messages.CONFRIM, reply_markup=keyboard)


@router.callback_query(F.data == Callbacks.CONFIRM)
@log_func
async def reschedule_lesson_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_confirm` state."""
    state_data = await state.get_data()
    weekdays = {d.weekday(): d for d in this_week()}
    with Session(engine) as session:
        sl: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
        reschedule = Reschedule(
            user=session.query(User).get(state_data["user_id"]),
            source=sl,
            source_date=weekdays[sl.weekday],
        )
        session.add(reschedule)
        session.commit()
    await callback.message.answer(Messages.LESSON_ADDED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
@log_func
async def reschedule_lesson_choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        user = session.query(User).get(state_data["user_id"])
        weekends = session.query(Weekend).filter(Weekend.teacher_id == user.teacher_id).all()
        weekends_str = ", ".join([config.WEEKDAY_MAP_FULL[w.weekday] for w in weekends])
    await state.set_state("reschedule_lesson_choose_date")
    await callback.message.answer(Messages.CHOOSE_DATE % weekends_str)


# TODO finish up

# date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True, default=None)
# start_time: Mapped[Optional[Time]] = mapped_column(Time, nullable=True, default=None)
# end_time: Mapped[Optional[Time]] = mapped_column(Time, nullable=True, default=None)

