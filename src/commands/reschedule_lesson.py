from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from database import engine
from help import Commands
from logger import log_func
from models import Reschedule, ScheduledLesson, User
from utils import MAX_HOUR, StudentSchedule, TeacherSchedule, inline_keyboard, this_week

COMMAND = "/reschedule"

router = Router()


class Messages:
    CHOOSE_LESSON = "Выберите занятие"
    NO_LESSONS = "Нет предстоящих занятий"
    CONFRIM = "Вы можете назначить новое время, чтобы перенести урок"
    CANCELED = "Урок отменён"
    CHOOSE_DATE = "Введите дату в формате ДД-ММ-ГГГГ, нельзя выбрать %s"
    WRONG_WEEKDAY = "Нельзя выбрать %s"
    CHOOSE_TIME = "Выберите время"
    LESSON_ADDED = "Урок добавлен"
    NOT_REGISTERED = "Вы не зарегистрированы. Пожалуйста воспользуйтесь командой /start"


class Callbacks:
    CHOOSE_SL = "reschesule_lesson_choose_sl:"
    CONFIRM = "reschesule_lesson_confirm:"
    CHOOSE_DATE = "reschesule_lesson_choose_date:"
    CHOOSE_TIME = "reschesule_lesson_choose_time:"


class ChooseNewDateTime(StatesGroup):
    choose_date = State()
    choose_time = State()


@router.message(Command(COMMAND))
@router.message(F.text == Commands.RESCHEDULE.value)
@log_func
async def reschedule_lesson_handler(message: Message) -> None:
    """Handler receives messages with `/reschedule` command."""
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            lessons = session.query(ScheduledLesson).filter(ScheduledLesson.user_id == user.id).all()
            if lessons:
                weekdays = {d.weekday(): config.WEEKDAY_MAP_FULL[d.weekday()] for d in this_week()}
                buttons = [
                    (
                        f"{weekdays[lesson.weekday]} {lesson.start_time}",
                        Callbacks.CHOOSE_SL + str(lesson.id),
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


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_SL))
@log_func
async def reschesule_lesson_choose_sl_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    lesson_id = int(callback.data.split(":")[1])
    with Session(engine) as session:
        lesson: ScheduledLesson = session.query(ScheduledLesson).get(lesson_id)
        if lesson:
            await state.update_data(lesson=lesson_id, user_id=lesson.user_id)
            keyboard = inline_keyboard(
                [
                    ("Отменить урок", Callbacks.CONFIRM),
                    ("Перенести на новую дату", Callbacks.CHOOSE_DATE),
                ],
            ).as_markup()
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
    await callback.message.answer(Messages.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
@log_func
async def reschedule_lesson_choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    state_data = await state.get_data()
    with Session(engine) as session:
        user: User = session.query(User).get(state_data["user_id"])
        schedule = TeacherSchedule(user) if user.teacher_id else StudentSchedule(user)
        weekends_str = ", ".join([config.WEEKDAY_MAP_FULL[w] for w in schedule.available_weekdays()])
    await state.set_state(ChooseNewDateTime.choose_date)
    await callback.message.answer(Messages.CHOOSE_DATE % weekends_str)


@router.message(ChooseNewDateTime.choose_date)
@log_func
async def reschedule_lesson_choose_time(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `reschedule_lesson_choose_time` state."""
    date = datetime.strptime(message.text, "%d-%m-%Y")  # noqa: DTZ007
    state_data = await state.get_data()
    with Session(engine) as session:
        user: User = session.query(User).get(state_data["user_id"])
        schedule = TeacherSchedule(user) if user.teacher_id else StudentSchedule(user)
        if date.weekday() not in schedule.available_weekdays():
            await message.answer(Messages.WRONG_WEEKDAY % config.WEEKDAY_MAP_FULL[date.weekday()])
            return
        await state.update_data(date=date)
        await state.set_state(ChooseNewDateTime.choose_time)
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
        reschedule = Reschedule(
            user=session.query(User).get(state_data["user_id"]),
            source=session.query(ScheduledLesson).get(state_data["lesson"]),
            source_date=state_data["date"],
            date=state_data["date"],
            start_time=time,
            end_time=time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0),
        )
        session.add(reschedule)
        session.commit()
    await callback.message.answer(Messages.LESSON_ADDED)