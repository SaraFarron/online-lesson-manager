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
from models import Teacher, Weekend, WorkBreak
from utils import inline_keyboard

COMMAND = "/reschedule"

router = Router()


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
    edit_weekends = State()
    add_break = State()


@router.message(Command(COMMAND))
@router.message(F.text == AdminCommands.EDIT_WORKING_HOURS.value)
@log_func
async def set_working_hours_handler(message: Message) -> None:
    """Handler receives messages with `/reschedule` command."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if not teacher:
            await message.answer(messages.PERMISSION_DENIED)
            return
        buttons = [
            (f"Изменить начало рабочего дня: {teacher.work_start.strftime('%H:%M')}", "swh:start"),
            (f"Изменить конец рабочего дня: {teacher.work_end.strftime('%H:%M')}", "swh:end"),
            *[
                (f"Убрать выходной: {config.WEEKDAY_MAP_FULL[weekend.weekday]}", f"swh:rm_weekend_{weekend.id}")
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
async def set_work_border_handler(message: Message, state: FSMContext) -> None:
    """Handler for changing the start of the work day."""
    try:
        time = datetime.strptime(message.text, "%H:%M").time()  # noqa: DTZ007
    except ValueError:
        await state.set_state(SetWorkingHoursState.edit_work_hours)
        await message.answer(Messages.WRONG_TIME_FORMAT)
        return
    state_data = await state.get_data()
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
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
    with Session(engine) as session:
        weekend_id = int(callback.data.replace("swh:rm_weekend_", ""))
        weekend = session.query(Weekend).get(weekend_id)
        if weekend:
            session.delete(weekend)
            session.commit()
    await callback.message.answer(Messages.WEEKEND_REMOVED)


@router.callback_query(F.data == "swh:add_weekend")
@log_func
async def add_weekend(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for adding a weekend."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        existing_weekends = [w.weekday for w in teacher.weekends]
        buttons = [(config.WEEKDAY_MAP_FULL[d], f"swh:add_weekend_{d}") for d in range(7) if d not in existing_weekends]
        keyboard = inline_keyboard(buttons)
        keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
        await callback.message.answer(Messages.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith("swh:add_weekend_"))
@log_func
async def add_weekend_finish(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for finishing adding a weekend."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
        weekend = Weekend(
            teacher_id=teacher.id,
            teacher=teacher,
            weekday=int(callback.data.replace("swh:add_weekend_", "")),
        )
        session.add(weekend)
        session.commit()
    await callback.message.answer(Messages.WEEKEND_ADDED)


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
