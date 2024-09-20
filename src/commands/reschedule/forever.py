from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.orm import Session

import messages
from commands.reschedule.config import FRL_START_CALLBACK, router
from config import config
from database import engine
from logger import log_func
from models import ScheduledLesson, User
from utils import MAX_HOUR, get_schedule, inline_keyboard, send_message


class Messages:
    CONFRIM = "Вы можете перенести урок на другое время или отменить его"
    CANCEL_LESSON = "Отменить урок"
    CHOOSE_NEW_DATE = "Перенести на новую дату"
    ALREADY_CANCELED = "Этот урок на эту дату уже отменён"
    CHOOSE_RIGHT_WEEKDAY = "Нельзя выбрать %s, подходят только даты на выбранный день недели - %s"
    CHOOSE_WEEKDAY = "Выберите день недели"
    WRONG_DATE = "Неправильный формат даты, введите дату в формате ДД-ММ-ГГГГ, например 01-01-2024"
    CANCELED = "Урок отменён"
    WRONG_WEEKDAY = "Нельзя выбрать %s"
    CHOOSE_TIME = "Выберите время (по МСК)"
    LESSON_ADDED = "Урок добавлен"


class Callbacks:
    CHOOSE_WEEKDAY = "frl_choose_weekday:"
    CONFIRM = "frl_confirm:"
    CHOOSE_DATE = "frl_choose_date:"
    CHOOSE_TIME = "frl_choose_time:"


@router.callback_query(F.data.startswith(FRL_START_CALLBACK))
@log_func
async def frl_cancel_or_reschedule(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        lesson: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
        if lesson:
            await state.update_data(
                lesson=state_data["lesson"],
                user_id=lesson.user_id,
                user_telegram_id=lesson.user.telegram_id,
            )
            keyboard = inline_keyboard(
                [
                    (Messages.CANCEL_LESSON, Callbacks.CONFIRM),
                    (Messages.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
                ],
            ).as_markup()
            await callback.message.answer(Messages.CONFRIM, reply_markup=keyboard)


@router.callback_query(F.data == Callbacks.CONFIRM)
@log_func
async def frl_delete_sl(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_confirm` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        sl: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
        user: User = session.query(User).get(state_data["user_id"])
        message = messages.USER_DELETED_SL % (
            user.username_dog,
            config.WEEKDAY_MAP_FULL[sl.weekday],
            sl.start_time.strftime("%H:%M"),
        )
        session.delete(sl)
        session.commit()
        await send_message(user.teacher.telegram_id, message)
    await state.clear()
    await callback.message.answer(Messages.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
@log_func
async def frl_choose_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    state_data = await state.get_data()
    with Session(engine):
        schedule = get_schedule(state_data["user_telegram_id"])
        weekdays = [(config.WEEKDAY_MAP[w], Callbacks.CHOOSE_WEEKDAY + str(w)) for w in schedule.available_weekdays()]
        keyboard = inline_keyboard(weekdays)
        await callback.message.answer(Messages.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
@log_func
async def frl_choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_time` state."""
    state_data = await state.get_data()
    date = int(callback.data.split(":")[1])

    with Session(engine):
        schedule = get_schedule(state_data["user_telegram_id"])
        weekday = date if isinstance(date, int) else date.weekday()
        if weekday not in schedule.available_weekdays():
            await callback.message.answer(Messages.WRONG_WEEKDAY % config.WEEKDAY_MAP_FULL[weekday])
            return
        await state.update_data(new_date=date)
        available_time = (
            schedule.available_time_weekday(date) if isinstance(date, int) else schedule.available_time_day(date)
        )
        buttons = [(t.strftime("%H:%M"), Callbacks.CHOOSE_TIME + t.strftime("%H.%M")) for t in available_time]
        keyboard = inline_keyboard(buttons)
        keyboard.adjust(2, repeat=True)
        await callback.message.answer(Messages.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
@log_func
async def frl_update_sl(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_create_reschedule` state."""
    state_data = await state.get_data()
    time = datetime.strptime(callback.data.split(":")[1], "%H.%M").time()  # noqa: DTZ007
    with Session(engine) as session:
        user: User = session.query(User).get(state_data["user_id"])
        sl: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
        old_w, old_t = sl.weekday, sl.start_time
        sl.weekday = state_data["new_date"]
        sl.start_time = time
        sl.end_time = time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0)
        message = messages.USER_MOVED_SL % (
            user.username_dog,
            config.WEEKDAY_MAP_FULL[old_w],
            old_t.strftime("%H:%M"),
            config.WEEKDAY_MAP_FULL[state_data["new_date"]],
            time.strftime("%H:%M"),
        )
        session.commit()
        await send_message(user.teacher.telegram_id, message)
    await callback.message.answer(Messages.LESSON_ADDED)
