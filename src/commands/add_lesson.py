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
from utils import inline_keyboard

COMMAND = "/add_lesson"
MAX_HOUR = 23

router = Router()


class Messages:
    CHOOSE_WEEKDAY = "Выберите день недели"
    CHOOSE_TIME = "Выберите время"
    LESSON_ADDED = "Урок добавлен"


class Callbacks:
    CHOOSE_WEEKDAY = "add_sl_choose_weekday:"
    CHOOSE_TIME = "add_sl_choose_time:"


def possible_weekdays_for_user(user_telegram_id: int) -> list[str]:
    """Return a list of available weekdays."""
    result = []
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == user_telegram_id).first()
        if not user:
            logger.warning("NO USER %s", user_telegram_id)
            return []
        for weekday in config.WEEKDAYS:
            # Check if any restrictions for this day
            restriced_periods = (
                session.query(RestrictedTime)
                .filter(
                    RestrictedTime.weekday == weekday,
                    RestrictedTime.user == user,
                )
                .all()
            )
            if any(period.whole_day_restricted for period in restriced_periods):
                continue
            result.append(weekday)
        return result


def possible_time_for_user(
    user_telegram_id: int,
    weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"],
) -> list[str]:
    """Return a list of available times."""
    result = []
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == user_telegram_id).first()
        if not user:
            logger.warning("NO USER %s", user_telegram_id)
            return []

        # Check if any restrictions for this day
        restriced_periods = (
            session.query(RestrictedTime)
            .filter(
                RestrictedTime.weekday == weekday,
                RestrictedTime.user == user,
            )
            .all()
        )
        taken_times = [(period.start_time, period.end_time) for period in restriced_periods]

        # Check if any lessons for this day
        lessons_this_day = session.query(ScheduledLesson).filter(ScheduledLesson.weekday == weekday).all()
        for lesson in lessons_this_day:
            taken_times.append((lesson.start_time, lesson.end_time))  # noqa: PERF401

        # Forming buttons for available time
        current_time: datetime = user.teacher.work_start
        while current_time < user.teacher.work_end:
            taken = False
            for taken_time in taken_times:
                if taken_time[0] <= current_time < taken_time[1]:
                    taken = True
                    break
            if not taken:
                result.append(current_time.strftime("%H.%M"))
            current_time = (
                current_time.replace(hour=current_time.hour + 1)
                if current_time.hour < MAX_HOUR
                else current_time.replace(hour=0)
            )

    return result


@router.message(Command(COMMAND))
@router.message(F.text == Commands.ADD_SCHEDULED_LESSON.value)
@log_func
async def add_lesson_handler(message: Message, state: FSMContext) -> None:
    """First handler, gives a list of available weekdays."""
    weekdays = [(d, Callbacks.CHOOSE_WEEKDAY + d) for d in possible_weekdays_for_user(message.from_user.id)]
    keyboard = inline_keyboard(weekdays)
    await state.update_data(user_id=message.from_user.id)
    await message.answer(Messages.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
@log_func
async def add_lesson_choose_weekday_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Second handler, gives a list of available times."""
    weekday = callback.data.split(":")[1]
    await state.update_data(weekday=weekday)
    state_data = await state.get_data()
    available_time = possible_time_for_user(state_data["user_id"], weekday)
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
            user=session.query(User).filter(User.telegram_id == state_data["user_id"]).first(),
            weekday=state_data["weekday"],
            start_time=time,
            end_time=time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0),
        )
        session.add(sl)
        session.commit()
    await callback.message.answer(Messages.LESSON_ADDED)
