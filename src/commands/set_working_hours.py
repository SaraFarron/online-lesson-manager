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
from help import AdminCommands
from logger import log_func
from models import Teacher, User
from utils import inline_keyboard

COMMAND = "/reschedule"

router = Router()


class Messages:
    SET_TIME = "Введите время в формате ЧЧ:ММ (МСК)"
    WRONG_TIME_FORMAT = "Неправильный формат времени, введите время в формате ЧЧ:ММ (МСК)"
    TIME_UPDATED = "Время обновлено"
    ERROR = "Произошла ошибка"


class SetWorkingHoursState(StatesGroup):
    edit_work_hours = State()
    edit_weekends = State()
    edit_breaks = State()


@router.message(Command(COMMAND))
@router.message(F.text == AdminCommands.EDIT_WORKING_HOURS.value)
@log_func
async def set_working_hours_handler(message: Message) -> None:
    """Handler receives messages with `/reschedule` command."""
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        teacher: Teacher | None = session.query(Teacher).get(user.teacher_id)
        if not teacher:
            await message.answer(messages.PERMISSION_DENIED)
            return
        buttons = [
            (f"Изменить начало рабочего дня: {teacher.work_start.strftime('%H:%M')}", "swh:start"),
            (f"Изменить конец рабочего дня: {teacher.work_end.strftime('%H:%M')}", "swh:end"),
            *[
                (f"Убрать выходной: {config.WEEKDAY_MAP_FULL[weekend.weekday]}", f"swh:rm_weekend_{weekend.weekday}")
                for weekend in teacher.weekends
            ],
            ("Добавить выходной", "swh:add_weekend"),
            ("Изменить перерывы", "swh:edit_breaks"),
        ]
        keyboard = inline_keyboard(buttons)
        keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
        await message.answer("Выберите действие", reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "swh:start")
@log_func
async def set_work_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for changing the start of the work day."""
    await state.update_data(scene="start")
    await state.set_state(SetWorkingHoursState.edit_work_hours)
    await callback.message.answer(Messages.SET_TIME)


@router.callback_query(F.data == "swh:end")
@log_func
async def set_work_end(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for changing the end of the work day."""
    await state.update_data(scene="end")
    await state.set_state(SetWorkingHoursState.edit_work_hours)
    await callback.message.answer(Messages.SET_TIME)


@router.message(SetWorkingHoursState.edit_work_hours)
@log_func
async def set_work_start_handler(message: Message, state: FSMContext) -> None:
    """Handler for changing the start of the work day."""
    try:
        time = datetime.strptime(message.text, "%H:%M").time()  # noqa: DTZ007
    except ValueError:
        await state.set_state(SetWorkingHoursState.edit_work_hours)
        await message.answer(Messages.WRONG_TIME_FORMAT)
        return
    state_data = await state.get_data()
    with Session(engine) as session:
        teacher: Teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if state_data["scene"] == "start":
            teacher.work_start = time
        elif state_data["scene"] == "end":
            teacher.work_end = time
        else:
            await message.answer(Messages.ERROR)
            return
        session.commit()
    await state.clear()
    await message.answer(Messages.TIME_UPDATED)


@router.callback_query(F.data.startswith("swh:rm_weekend_"))
@log_func
async def remove_weekend(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for removing a weekend."""
    # TODO


@router.callback_query(F.data == "swh:add_weekend")
@log_func
async def add_weekend(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for adding a weekend."""
    # TODO


@router.callback_query(F.data == "swh:edit_breaks")
@log_func
async def edit_breaks(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for editing breaks."""
    # TODO
