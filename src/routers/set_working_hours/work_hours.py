from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from logger import log_func
from models import Teacher
from routers.set_working_hours.config import router


class Messages:
    SET_TIME = "Введите время в формате ЧЧ:ММ (МСК)"
    WRONG_TIME_FORMAT = "Неправильный формат времени, введите время в формате ЧЧ:ММ (МСК)"
    TIME_UPDATED = "Время обновлено"
    ERROR = "Произошла ошибка"
    WEEKEND_REMOVED = "Выходной убран"
    CHOOSE_WEEKDAY = "Выберите день недели"
    WEEKEND_ADDED = "Выходной добавлен"
    EDIT_BREAKS = "Выберите действие"
    CHOOSE_BREAK_PERIOD = "Напишите время перерыва в формате ЧЧ:ММ - ЧЧ:ММ (МСК)"
    INVALID_TIME_PERIOD = "Неправильный формат времени, введите время в формате 'ЧЧ:ММ - ЧЧ:ММ' (МСК)"
    BREAK_REMOVED = "Перерыв убран"
    BREAK_ADDED = "Перерыв добавлен"


class SetWorkingHoursState(StatesGroup):
    edit_work_hours = State()


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
async def set_work_border_handler(message: Message, state: FSMContext, db: Session) -> None:
    """Handler for changing the start of the work day."""
    try:
        time = datetime.strptime(message.text, "%H:%M").time()  # noqa: DTZ007
    except ValueError:
        await state.set_state(SetWorkingHoursState.edit_work_hours)
        await message.answer(Messages.WRONG_TIME_FORMAT)
        return
    state_data = await state.get_data()
    teacher = db.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
    if state_data["scene"] == "start":
        teacher.work_start = time
    elif state_data["scene"] == "end":
        teacher.work_end = time
    else:
        await message.answer(Messages.ERROR)
        return
    db.commit()
    await state.clear()
    await message.answer(Messages.TIME_UPDATED)
