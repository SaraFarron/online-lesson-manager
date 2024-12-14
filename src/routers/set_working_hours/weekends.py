from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError, PermissionDeniedError
from messages import replies
from models import Teacher, Weekend
from routers.set_working_hours.config import router
from utils import inline_keyboard


@router.callback_query(F.data.startswith("swh:rm_weekend_"))
async def remove_weekend(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for removing a weekend."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    weekend_id = int(callback.data.replace("swh:rm_weekend_", ""))  # type: ignore  # noqa: PGH003
    weekend = db.query(Weekend).get(weekend_id)
    if weekend:
        db.delete(weekend)
        db.commit()
    await callback.message.answer(replies.WEEKEND_REMOVED)


@router.callback_query(F.data == "swh:add_weekend")
async def add_weekend(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for adding a weekend."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if not teacher:
        raise PermissionDeniedError
    existing_weekends = [w.weekday for w in teacher.weekends]
    buttons = [(config.WEEKDAY_MAP_FULL[d], f"swh:add_weekend_{d}") for d in range(7) if d not in existing_weekends]
    keyboard = inline_keyboard(buttons)
    keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await callback.message.answer(replies.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith("swh:add_weekend_"))
async def add_weekend_finish(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for finishing adding a weekend."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if not teacher:
        raise PermissionDeniedError
    weekend = Weekend(
        teacher_id=teacher.id,
        teacher=teacher,
        weekday=int(callback.data.replace("swh:add_weekend_", "")),  # type: ignore  # noqa: PGH003
    )
    db.add(weekend)
    db.commit()
    await callback.message.answer(replies.WEEKEND_ADDED)
