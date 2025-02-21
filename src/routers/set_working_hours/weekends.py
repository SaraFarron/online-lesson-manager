from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session
from service import Service
from errors import AiogramTelegramError
from messages import replies
from routers.set_working_hours.config import router
from utils import inline_keyboard


@router.callback_query(F.data.startswith("swh:rm_weekend_"))
async def remove_weekend(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for removing a weekend."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    teacher = service.get_teacher(message.from_user.id)

    service.delete_weekend(teacher, int(callback.data.replace("swh:rm_weekend_", "")))
    db.commit()
    await message.answer(replies.WEEKEND_REMOVED)


@router.callback_query(F.data == "swh:add_weekend")
async def add_weekend(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for adding a weekend."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    teacher = service.get_teacher(message.from_user.id)

    existing_weekends = service.get_weekends(teacher)
    keyboard = inline_keyboard(existing_weekends)

    await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith("swh:add_weekend_"))
async def add_weekend_finish(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for finishing adding a weekend."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    teacher = service.get_teacher(message.from_user.id)
    service.create_weekend(teacher, int(callback.data.replace("swh:add_weekend_", "")))
    db.commit()
    await message.answer(replies.WEEKEND_ADDED)
