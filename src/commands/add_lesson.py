from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from database import engine
from help import Commands
from logger import log_func
from models import ScheduledLesson
from utils import StudentSchedule, TeacherSchedule, get_user, inline_keyboard

COMMAND = "/add_sl"
MAX_HOUR = 23

router = Router()


class Messages:
    CHOOSE_WEEKDAY = "Выберите день недели"
    CHOOSE_TIME = "Выберите время"
    LESSON_ADDED = "Урок добавлен"


class Callbacks:
    CHOOSE_WEEKDAY = "add_sl_choose_weekday:"
    CHOOSE_TIME = "add_sl_choose_time:"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.ADD_SCHEDULED_LESSON.value)
@log_func
async def add_lesson_handler(message: Message, state: FSMContext) -> None:
    """First handler, gives a list of available weekdays."""
    with Session(engine):
        user = get_user(message.from_user.id)
        schedule = TeacherSchedule(user) if user.teacher_id else StudentSchedule(user)
        available_weekdays = schedule.available_weekdays()

    weekdays = [(config.WEEKDAY_MAP[d], Callbacks.CHOOSE_WEEKDAY + str(d)) for d in available_weekdays]
    keyboard = inline_keyboard(weekdays)

    await state.update_data(user_id=message.from_user.id)
    await message.answer(Messages.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
@log_func
async def add_lesson_choose_weekday_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Second handler, gives a list of available times."""
    weekday = int(callback.data.split(":")[1])
    await state.update_data(weekday=weekday)
    state_data = await state.get_data()

    with Session(engine):
        user = get_user(state_data["user_id"])
        schedule = TeacherSchedule(user) if user.teacher_id else StudentSchedule(user)
        available_time = schedule.available_time_weekday(weekday)

    keyboard = inline_keyboard([(t, Callbacks.CHOOSE_TIME + t) for t in available_time])
    keyboard.adjust(1, repeat=True)

    await callback.message.answer(Messages.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
@log_func
async def add_lesson_choose_time_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Last handler, saves scheduled lesson."""
    time_str = callback.data.split(":")[1]
    time = datetime.strptime(time_str, "%H.%M").time()  # noqa: DTZ007
    state_data = await state.get_data()
    await state.update_data(time=time)
    with Session(engine) as session:
        sl = ScheduledLesson(
            user=get_user(state_data["user_id"]),
            weekday=state_data["weekday"],
            start_time=time,
            end_time=time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0),
        )
        session.add(sl)
        session.commit()
    await callback.message.answer(Messages.LESSON_ADDED)