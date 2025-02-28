from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.orm import Session

from routers import callbacks
from routers.reschedule.config import router
from service import Service
from utils import telegram_checks


@router.callback_query(F.data.startswith(callbacks.Reschedule.choose_date))
async def choose_date(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    service.get_user(message.from_user.id)

    # Choose time


@router.callback_query(F.data.startswith(callbacks.Reschedule.choose_date))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    service.get_user(message.from_user.id)

    # Finish
