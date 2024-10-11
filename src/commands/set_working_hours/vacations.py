from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

import messages
from commands.set_working_hours.config import router
from config import config
from database import engine
from logger import log_func
from models import Teacher, Vacations
from utils import inline_keyboard


class Messages:
    EDIT_VACATIONS = "Выберите действие"
    CHOOSE_START = "Введите дату начала отпуска в формате ДД-ММ-ГГГГ"
    CHOOSE_END = "Введите дату окончания отпуска в формате ДД-ММ-ГГГГ"
    INVALID_DATE = "Введите дату в формате ДД-ММ-ГГГГ (например 01-01-2022)"
    START_IS_AFTER_END = "Дата начала отпуска не может быть позже даты окончания"
    VACATION_ADDED = "Отпуск добавлен"
    VACATION_DELETED = "Отпуск удален"


class States(StatesGroup):
    edit_vacation = State()
    add_vacation_start = State()
    add_vacation_end = State()
    edit_vacation_date = State()
    edit_vacation_time = State()


@router.callback_query(F.data.startswith("swh:edit_vacations"))
@log_func
async def edit_vacations_hanlder(callback: CallbackQuery, state: FSMContext):
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        if teacher is None:
            await callback.message.answer(messages.PERMISSION_DENIED)
            return
        buttons = [
            (f"Убрать отпуск {h.start_date} - {h.end_date}", f"swh:rm_vacation_{h.id}") for h in teacher.holidays
        ]
        buttons.append(("Добавить отпуск", "swh:add_vacation_start"))
        keyboard = inline_keyboard(buttons)
        keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
        await state.update_data(teacher=teacher)
        await callback.message.answer(Messages.EDIT_VACATIONS, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "swh:add_vacation_start")
@log_func
async def add_vacation_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for adding vacations."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        if teacher is None:
            await callback.message.answer(messages.PERMISSION_DENIED)
            return
        await state.set_state(States.add_vacation_start)
        await callback.message.answer(Messages.CHOOSE_START)


@router.message(States.add_vacation_start)
@log_func
async def add_vacation_end(message: Message, state: FSMContext) -> None:
    """Handler for adding vacations."""
    try:
        start = datetime.strptime(message.text.split(":")[-1], "%d-%m-%Y").date()
    except ValueError:
        await message.answer(Messages.START_IS_AFTER_END)
        await state.clear()
        await message.answer("Операция отменена")
        return
    await state.update_data(start=start)
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if teacher is None:
            await message.answer(messages.PERMISSION_DENIED)
            return
        await state.set_state(States.add_vacation_end)
        await message.answer(Messages.CHOOSE_END)


@router.message(States.add_vacation_end)
@log_func
async def add_vacation_finish(message: Message, state: FSMContext) -> None:
    """Handler for adding vacations."""
    try:
        end = datetime.strptime(message.text.split(":")[-1], "%d-%m-%Y").date()
    except ValueError:
        await message.answer(Messages.INVALID_DATE)
        await state.set_state(States.add_vacation_start)
        return
    state_data = await state.get_data()
    if state_data["start"] > end:
        await message.answer(Messages.START_IS_AFTER_END)
        await state.set_state(States.add_vacation_start)
        return
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if teacher is None:
            await message.answer(messages.PERMISSION_DENIED)
            return
        vacation = Vacations(start_date=state_data["start"], end_date=end, teacher=teacher)
        session.add(vacation)
        session.commit()
    await state.clear()
    await message.answer(Messages.VACATION_ADDED)


@router.callback_query(F.data.startswith("swh:rm_vacation_"))
@log_func
async def remove_break(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for removing breaks."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        if teacher is None:
            await callback.message.answer(messages.PERMISSION_DENIED)
            return
        wb = session.query(Vacations).filter(Vacations.id == int(callback.data.replace("swh:rm_vacation_", ""))).first()
        if wb:
            session.delete(wb)
        session.commit()

    await callback.message.answer(Messages.VACATION_DELETED)
