from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

import messages
from commands.reschedule.config import ORL_RS_CALLBACK, ORL_START_CALLBACK, router
from config import config
from database import engine
from logger import log_func
from models import Reschedule, ScheduledLesson, User
from utils import calc_end_time, get_schedule, inline_keyboard, send_message


class ChooseNewDateTime(StatesGroup):
    date = State()
    time = State()


class Messages:
    CONFRIM = "Вы можете перенести урок на другое время или отменить его"
    CANCEL_LESSON = "Отменить урок"
    CHOOSE_NEW_DATE = "Перенести на новую дату"
    ALREADY_CANCELED = "Этот урок на эту дату уже отменён"
    CHOOSE_RIGHT_WEEKDAY = "Нельзя выбрать %s, подходят только даты на выбранный день недели - %s"
    TYPE_NEW_DATE = "Введите дату в формате ДД-ММ-ГГГГ, в которую хотите отменить занятие"
    WRONG_DATE = "Неправильный формат даты, введите дату в формате ДД-ММ-ГГГГ, например 01-01-2024"
    CANCELED = "Урок отменён"
    CHOOSE_DATE = "Введите дату в формате ДД-ММ-ГГГГ, можно выбрать следующие дни недели: %s"
    WRONG_WEEKDAY = "Нельзя выбрать %s"
    CHOOSE_TIME = "Выберите время (по МСК)"
    LESSON_ADDED = "Урок добавлен"


class Callbacks:
    CHOOSE_WEEKDAY = "orl_choose_weekday:"
    CONFIRM = "orl_confirm:"
    CHOOSE_DATE = "orl_choose_date:"
    CHOOSE_TIME = "orl_choose_time:"


@router.callback_query(F.data.startswith(ORL_START_CALLBACK))
@log_func
async def orl_type_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        lesson: ScheduledLesson | None = session.query(ScheduledLesson).get(state_data["lesson"])
        if lesson:
            await state.update_data(
                event=lesson,
                user_id=lesson.user_id,
                user_telegram_id=lesson.user.telegram_id,
            )
            await state.set_state(ChooseNewDateTime.date)
            await callback.message.answer(Messages.TYPE_NEW_DATE)
        else:
            await state.clear()
            await callback.message.answer("Произошла непредвиденная ошибка")


@router.callback_query(F.data.startswith(ORL_RS_CALLBACK))
@log_func
async def orl_rs_cancel_or_reschedule(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    with Session(engine) as session:
        event: Reschedule = session.query(Reschedule).get(int(callback.data.split(":")[2]))
        await state.update_data(date=event.source_date, event=event, user_telegram_id=event.user.telegram_id)
    keyboard = inline_keyboard(
        [
            (Messages.CANCEL_LESSON, Callbacks.CONFIRM),
            (Messages.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
        ],
    ).as_markup()
    await callback.message.answer(Messages.CONFRIM, reply_markup=keyboard)


@router.message(ChooseNewDateTime.date)
@log_func
async def orl_cancel_or_reschedule(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    try:
        date = datetime.strptime(message.text, "%d-%m-%Y")  # noqa: DTZ007
    except ValueError:
        await state.set_state(ChooseNewDateTime.date)
        await message.answer(Messages.WRONG_DATE)
        return
    state_data = await state.get_data()
    with Session(engine) as session:
        reschedules = session.query(Reschedule).filter(Reschedule.source_date == date.date()).all()
        rshs: list[Reschedule] = []
        for r in reschedules:
            if r.source is None:
                session.delete(r)
                session.commit()
            rshs.append(r)
        event = state_data["event"]
        if isinstance(event, ScheduledLesson) and event.id in [r.source.id for r in rshs]:
            await message.answer(Messages.ALREADY_CANCELED)
            await state.clear()
            return
        if isinstance(event, ScheduledLesson):
            right_weekday = session.query(ScheduledLesson).get(state_data["lesson"]).weekday
            if date.weekday() != right_weekday:
                await state.set_state(ChooseNewDateTime.date)
                await message.answer(
                    Messages.CHOOSE_RIGHT_WEEKDAY
                    % (config.WEEKDAY_MAP_FULL[date.weekday()], config.WEEKDAY_MAP_FULL[right_weekday]),
                )
                return

    await state.update_data(date=date)
    keyboard = inline_keyboard(
        [
            (Messages.CANCEL_LESSON, Callbacks.CONFIRM),
            (Messages.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
        ],
    ).as_markup()
    await message.answer(Messages.CONFRIM, reply_markup=keyboard)


@router.callback_query(F.data == Callbacks.CONFIRM)
@log_func
async def orl_cancel_lesson(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_confirm` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        user: User = session.query(User).get(state_data["user_id"])
        if isinstance(state_data["event"], Reschedule):
            event = session.query(Reschedule).get(state_data["event"].id)
            session.delete(event)
        else:
            event: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
            reschedule = Reschedule(
                user=user,
                source=event,
                source_date=state_data["date"],
            )
            session.add(reschedule)
        message = messages.USER_CANCELED_SL % (
            user.username_dog,
            state_data["date"].strftime("%d-%m-%Y"),
            event.st_str,
        )
        session.commit()
        await send_message(user.teacher.telegram_id, message)
    await state.clear()
    await callback.message.answer(Messages.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
@log_func
async def orl_choose_new_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    state_data = await state.get_data()
    with Session(engine):
        schedule = get_schedule(state_data["user_telegram_id"])
        weekends_str = ", ".join([config.WEEKDAY_MAP_FULL[w] for w in schedule.available_weekdays()])
        await state.set_state(ChooseNewDateTime.time)
        await callback.message.answer(Messages.CHOOSE_DATE % weekends_str)


@router.message(ChooseNewDateTime.time)
@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
@log_func
async def orl_choose_time(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_time` state."""
    state_data = await state.get_data()
    try:
        date = datetime.strptime(message.text, "%d-%m-%Y")  # noqa: DTZ007
    except ValueError:
        await state.set_state(ChooseNewDateTime.time)
        await message.answer(Messages.WRONG_DATE)
        return
    await state.update_data(new_date=date)

    with Session(engine):
        schedule = get_schedule(state_data["user_telegram_id"])
        weekday = date if isinstance(date, int) else date.weekday()
        if weekday not in schedule.available_weekdays():
            await message.answer(Messages.WRONG_WEEKDAY % config.WEEKDAY_MAP_FULL[weekday])
            return
        await state.update_data(new_date=date)
        await state.set_state(ChooseNewDateTime.time)
        available_time = (
            schedule.available_time_weekday(date) if isinstance(date, int) else schedule.available_time_day(date)
        )
        buttons = [(t.strftime("%H:%M"), Callbacks.CHOOSE_TIME + t.strftime("%H.%M")) for t in available_time]
        keyboard = inline_keyboard(buttons)
        keyboard.adjust(2, repeat=True)
        await message.answer(Messages.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
@log_func
async def reschedule_lesson_create_reschedule(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_create_reschedule` state."""
    state_data = await state.get_data()
    time = datetime.strptime(callback.data.split(":")[1], "%H.%M").time()  # noqa: DTZ007
    with Session(engine) as session:
        user: User = session.query(User).get(state_data["user_id"])
        event = state_data["event"]
        if isinstance(event, Reschedule):
            event: Reschedule = session.query(Reschedule).get(event.id)
            old_date, old_time = event.source_date, event.start_time
            event.date = state_data["new_date"]
            event.start_time = time
            event.end_time = calc_end_time(time)
        else:
            sl: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
            reschedule = Reschedule(
                user=user,
                source=sl,
                source_date=state_data["date"],
                date=state_data["new_date"],
                start_time=time,
                end_time=calc_end_time(time),
            )
            old_date, old_time = reschedule.source_date.strftime("%d-%m-%Y"), sl.st_str
            session.add(reschedule)
        message = messages.USER_MOVED_SL % (
            user.username_dog,
            old_date,
            old_time,
            state_data["new_date"].strftime("%d-%m-%Y"),
            time.strftime("%H:%M"),
        )
        session.commit()
        await send_message(user.teacher.telegram_id, message)
    await callback.message.answer(Messages.LESSON_ADDED)
