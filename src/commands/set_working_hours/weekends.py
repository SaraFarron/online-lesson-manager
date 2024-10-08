from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.orm import Session

from commands.set_working_hours.config import router
from config import config
from database import engine
from logger import log_func
from models import Teacher, Weekend
from utils import inline_keyboard


class Messages:
    WEEKEND_REMOVED = "Выходной убран"
    CHOOSE_WEEKDAY = "Выберите день недели"
    WEEKEND_ADDED = "Выходной добавлен"


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
