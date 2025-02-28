from __future__ import annotations

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.orm import Session

from routers import callbacks
from routers.reschedule.config import router
from service import Service
from utils import telegram_checks


@router.callback_query(F.data.startswith(callbacks.Reschedule.choose_lesson_rs))
async def choose_lesson_rs(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    service.get_user(message.from_user.id)

    # Choose do you need new date/time


@router.callback_query(F.data.startswith(callbacks.Reschedule.choose_lesson_ls))
async def choose_lesson_ls(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    service.get_user(message.from_user.id)

    # Choose do you need new date/time


@router.callback_query(F.data.startswith(callbacks.Reschedule.choose_lesson_sl))
async def choose_lesson_sl(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    service.get_user(message.from_user.id)

    # Choose entity


@router.callback_query(F.data.startswith(callbacks.Reschedule.choose_sl_entity))
async def choose_sl_entity(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    service.get_user(message.from_user.id)

    # Next/previous
    # Choose do you need new date/time
