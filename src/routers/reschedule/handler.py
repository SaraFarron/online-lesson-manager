from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import config
from help import Commands
from messages import replies
from db.models import RecurrentEvent
from routers import callbacks
from routers.reschedule.config import router
from service import Service,Keyboards
from utils import telegram_checks

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
            key = callbacks.RescheduleCallback.choose_lesson_sl
        elif event.is_reschedule:
            key = callbacks.RescheduleCallback.choose_lesson_rs
        else:
            key = callbacks.RescheduleCallback.choose_lesson_ls
        kb_buttons[f"{key}{event.id}"] = str(event)
    keyboard = Keyboards.inline_keyboard(kb_buttons)

    await message.answer(replies.CHOOSE_LESSON, reply_markup=keyboard)
