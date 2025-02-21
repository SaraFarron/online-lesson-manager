from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session
from service import Service
from config import config
from routers import callbacks
from help import Commands
from models import Event, RecurrentEvent
from messages import buttons, replies
from routers.reschedule.config import FRL_START_CALLBACK, ORL_START_CALLBACK, router
from utils import inline_keyboard, telegram_checks, callback_buttons

COMMAND = "/reschedule"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.RESCHEDULE.value)
async def reschedule(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `/reschedule` command."""
    message = telegram_checks(message)

    service = Service(db)
    user = service.get_user(message.from_user.id)

    cancellable_events = service.events_to_cancel(user, datetime.now(config.TIMEZONE).date())
    if not cancellable_events:
        await message.answer(replies.NO_LESSONS)
        return

    kb_buttons = {}
    for event in cancellable_events:
        if isinstance(event, RecurrentEvent):
            key = callbacks.Reschedule.choose_lesson_sl
        elif event.is_reschedule:
            key = callbacks.Reschedule.choose_lesson_rs
        else:
            key = callbacks.Reschedule.choose_lesson_ls
        kb_buttons[f"{key}{event.id}"] = str(event)
    keyboard = inline_keyboard(kb_buttons)

    await message.answer(replies.CHOOSE_LESSON, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(callbacks.Reschedule.choose_lesson))
async def choose_lesson(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    message = telegram_checks(callback)

    service = Service(db)
    service.get_user(message.from_user.id)

    lesson_id = int(callback.data.split(":")[1])  # type: ignore  # noqa: PGH003
    await state.update_data(lesson=lesson_id)
    btns = [
        (buttons.ONE_TYPE, ORL_START_CALLBACK),
        (buttons.DELETE_TYPE, FRL_START_CALLBACK),
    ]
    keyboard = inline_keyboard(btns)

    await message.answer(replies.CANCEL_TYPE, reply_markup=keyboard.as_markup())
