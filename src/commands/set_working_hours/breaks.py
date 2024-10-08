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
from models import Teacher, WorkBreak
from utils import inline_keyboard


class Messages:
    CHOOSE_WEEKDAY = "Выберите день недели"
    EDIT_BREAKS = "Выберите действие"
    CHOOSE_BREAK_PERIOD = "Напишите время перерыва в формате ЧЧ:ММ - ЧЧ:ММ (МСК)"
    INVALID_TIME_PERIOD = "Неправильный формат времени, введите время в формате 'ЧЧ:ММ - ЧЧ:ММ' (МСК)"
    BREAK_REMOVED = "Перерыв убран"
    BREAK_ADDED = "Перерыв добавлен"


class SetWorkingHoursState(StatesGroup):
    add_break = State()


@router.callback_query(F.data == "swh:edit_breaks")
@log_func
async def edit_breaks(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for editing breaks."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        if teacher is None:
            await callback.message.answer(messages.PERMISSION_DENIED)
            return

        breaks = session.query(WorkBreak).filter(WorkBreak.teacher_id == teacher.id).all()
        breaks = [
            (
                f"Убрать: {b.weekday_short_str} {b.st_str}-{b.et_str}",
                f"swh:rm_break_{b.id}",
            )
            for b in breaks
        ]

        keyboard = inline_keyboard([*breaks, ("Добавить перерыв", "swh:add_break")])
        keyboard.adjust(1 if len(breaks) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
        await callback.message.answer(Messages.EDIT_BREAKS, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "swh:add_break")
@log_func
async def add_break(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for adding breaks."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        if teacher is None:
            await callback.message.answer(messages.PERMISSION_DENIED)
            return
        await callback.message.answer(
            Messages.CHOOSE_WEEKDAY,
            reply_markup=inline_keyboard(
                [
                    (config.WEEKDAY_MAP[d], f"swh:add_break_{d}")
                    for d in range(7)
                    if d not in [w.weekday for w in teacher.weekends]
                ],
            ).as_markup(),
        )


@router.callback_query(F.data.startswith("swh:add_break_"))
@log_func
async def add_break_choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for finishing adding breaks."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        if teacher is None:
            await callback.message.answer(messages.PERMISSION_DENIED)
            return
        await state.update_data(weekday=int(callback.data.replace("swh:add_break_", "")))
        await state.set_state(SetWorkingHoursState.add_break)
        await callback.message.answer(Messages.CHOOSE_BREAK_PERIOD)


@router.message(SetWorkingHoursState.add_break)
@log_func
async def add_break_finish(message: Message, state: FSMContext) -> None:
    """Handler for finishing adding breaks."""
    data = await state.get_data()
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if teacher is None:
            await message.answer(messages.PERMISSION_DENIED)
            return
        try:
            start_time = datetime.strptime(message.text.split(" - ")[0], "%H:%M").time()  # noqa: DTZ007
            end_time = datetime.strptime(message.text.split(" - ")[1], "%H:%M").time()  # noqa: DTZ007
        except ValueError:
            await message.answer(Messages.INVALID_TIME_PERIOD)
            return
        work_break = WorkBreak(
            teacher_id=teacher.id,
            teacher=teacher,
            weekday=data["weekday"],
            start_time=start_time,
            end_time=end_time,
        )
        session.add(work_break)
        session.commit()

    await message.answer(Messages.BREAK_ADDED)


@router.callback_query(F.data.startswith("swh:rm_break_"))
@log_func
async def remove_break(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for removing breaks."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        if teacher is None:
            await callback.message.answer(messages.PERMISSION_DENIED)
            return
        wb = session.query(WorkBreak).filter(WorkBreak.id == int(callback.data.replace("swh:rm_break_", ""))).first()
        if wb:
            session.delete(wb)
        session.commit()

    await callback.message.answer(Messages.BREAK_REMOVED)
