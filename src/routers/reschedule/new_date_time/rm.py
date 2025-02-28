from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.orm import Session

from routers import callbacks
from routers.reschedule.config import router
from service import Service
from utils import telegram_checks


@router.callback_query(F.data.startswith(callbacks.Reschedule.rm_cancel))
async def rm_event(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    service.get_user(message.from_user.id)

    # Finish
