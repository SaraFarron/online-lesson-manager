from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

import messages
from config import config
from database import engine
from help import Commands
from logger import log_func
from models import Reschedule, ScheduledLesson, User
from utils import MAX_HOUR, get_schedule, inline_keyboard, send_message, this_week

COMMAND = "/reschedule"

router = Router()


class Messages:
    CHOOSE_LESSON = "Выберите занятие"
    NO_LESSONS = "Нет предстоящих занятий"
    CANCEL_TYPE = "Вы можете отменить/перенести занятие навсегда или только в какую-то дату"
    DELETE_TYPE = "Навсегда"
    ONE_TYPE = "Только на одну дату"
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
    NOT_REGISTERED = "Вы не зарегистрированы. Пожалуйста воспользуйтесь командой /start"


class Callbacks:
    CHOOSE_SL = "reschesule_lesson_choose_sl:"
    CHOOSE_CANCEL_TYPE = "reschesule_lesson_choose_cancel_type:"
    DELETE = "reschesule_lesson_delete:"
    CHOOSE_SL_DATE = "reschesule_lesson_choose_date_sl:"
    CONFIRM = "reschesule_lesson_confirm:"
    CHOOSE_DATE = "reschesule_lesson_choose_date:"
    CHOOSE_TIME = "reschesule_lesson_choose_time:"


class ChooseNewDateTime(StatesGroup):
    lesson_date = State()
    date = State()
    time = State()


@router.message(Command(COMMAND))
@router.message(F.text == Commands.RESCHEDULE.value)
@log_func
async def reschedule_lesson_handler(message: Message) -> None:
    """Handler receives messages with `/reschedule` command."""
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            lessons = (
                session.query(ScheduledLesson)
                .filter(ScheduledLesson.user_id == user.id)
                .order_by(ScheduledLesson.weekday, ScheduledLesson.start_time)
                .all()
            )
            if lessons:
                weekdays = {d.weekday(): config.WEEKDAY_MAP_FULL[d.weekday()] for d in this_week()}
                buttons = [
                    (
                        f"{weekdays[lesson.weekday]} {lesson.start_time}",
                        Callbacks.CHOOSE_CANCEL_TYPE + str(lesson.id),
                    )
                    for lesson in lessons
                ]
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
    keyboard = inline_keyboard(
        [
            (Messages.ONE_TYPE, Callbacks.CHOOSE_SL_DATE),
            (Messages.DELETE_TYPE, Callbacks.DELETE),
        ]
    )
    keyboard.adjust(1 if len(keyboard) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await callback.message.answer(Messages.CANCEL_TYPE, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == Callbacks.DELETE)
@log_func
async def reschedule_lesson_delete_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        lesson: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
        if lesson:
            session.delete(lesson)
            session.commit()
    await state.clear()
    await callback.message.answer(Messages.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_SL_DATE)
@log_func
async def reschedule_lesson_choose_sl_date_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        lesson: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
        if lesson:
            await state.update_data(
                lesson=state_data["lesson"], user_id=lesson.user_id, user_telegram_id=lesson.user.telegram_id
            )
            await state.set_state(ChooseNewDateTime.lesson_date)
            await callback.message.answer(Messages.TYPE_NEW_DATE)


@router.message(ChooseNewDateTime.lesson_date)
@log_func
async def reschesule_lesson_choose_sl_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    try:
        date = datetime.strptime(message.text, "%d-%m-%Y")  # noqa: DTZ007
    except ValueError:
        await state.set_state(ChooseNewDateTime.lesson_date)
        await message.answer(Messages.WRONG_DATE)
        return
    state_data = await state.get_data()
    with Session(engine) as session:
        reschedules = session.query(Reschedule).filter(Reschedule.source_date == date.date()).all()
        if state_data["lesson"] in [r.source.id for r in reschedules]:
            await message.answer(Messages.ALREADY_CANCELED)
            await state.clear()
            return
        right_weekday = session.query(ScheduledLesson).get(state_data["lesson"]).weekday
        if date.weekday() != right_weekday:
            await state.set_state(ChooseNewDateTime.lesson_date)
            await message.answer(
                Messages.CHOOSE_RIGHT_WEEKDAY
                % (config.WEEKDAY_MAP_FULL[date.weekday()], config.WEEKDAY_MAP_FULL[right_weekday]),
            )

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
async def reschedule_lesson_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_confirm` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        sl: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
        user: User = session.query(User).get(state_data["user_id"])
        reschedule = Reschedule(
            user=user,
            source=sl,
            source_date=state_data["date"],
        )
        session.add(reschedule)
        session.commit()
        await send_message(
            user.teacher.telegram_id,
            messages.USER_CANCELED_SL
            % (user.name, state_data["date"].strftime("%d-%m-%Y"), sl.start_time.strftime("%H:%M")),
        )
    await callback.message.answer(Messages.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
@log_func
async def reschedule_lesson_choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    state_data = await state.get_data()
    with Session(engine):
        schedule = get_schedule(state_data["user_telegram_id"])
        weekends_str = ", ".join([config.WEEKDAY_MAP_FULL[w] for w in schedule.available_weekdays()])
    await state.set_state(ChooseNewDateTime.date)
    await callback.message.answer(Messages.CHOOSE_DATE % weekends_str)


@router.message(ChooseNewDateTime.date)
@log_func
async def reschedule_lesson_choose_time(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_time` state."""
    try:
        date = datetime.strptime(message.text, "%d-%m-%Y")  # noqa: DTZ007
    except ValueError:
        await state.set_state(ChooseNewDateTime.date)
        await message.answer(Messages.WRONG_DATE)
        return

    state_data = await state.get_data()
    with Session(engine):
        schedule = get_schedule(state_data["user_telegram_id"])
        if date.weekday() not in schedule.available_weekdays():
            await message.answer(Messages.WRONG_WEEKDAY % config.WEEKDAY_MAP_FULL[date.weekday()])
            return
        await state.update_data(new_date=date)
        await state.set_state(ChooseNewDateTime.time)
        buttons = [
            (t.strftime("%H:%M"), Callbacks.CHOOSE_TIME + t.strftime("%H.%M"))
            for t in schedule.available_time_day(date)
        ]
        await message.answer(Messages.CHOOSE_TIME, reply_markup=inline_keyboard(buttons).as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
@log_func
async def reschedule_lesson_create_reschedule(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_create_reschedule` state."""
    state_data = await state.get_data()
    time = datetime.strptime(callback.data.split(":")[1], "%H.%M").time()  # noqa: DTZ007
    with Session(engine) as session:
        user: User = session.query(User).get(state_data["user_id"])
        sl: ScheduledLesson = session.query(ScheduledLesson).get(state_data["lesson"])
        reschedule = Reschedule(
            user=user,
            source=sl,
            source_date=state_data["date"],
            date=state_data["new_date"],
            start_time=time,
            end_time=time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0),
        )
        session.add(reschedule)
        session.commit()
        await send_message(
            user.teacher.telegram_id,
            messages.USER_MOVED_SL
            % (
                user.name,
                reschedule.source_date.strftime("%d-%m-%Y"),
                sl.start_time.strftime("%H:%M"),
                state_data["new_date"].strftime("%d-%m-%Y"),
                time.strftime("%H:%M"),
            ),
        )
    await callback.message.answer(Messages.LESSON_ADDED)
