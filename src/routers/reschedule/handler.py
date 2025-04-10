from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError
from help import Commands
from messages import buttons, replies
from models import Reschedule, Lesson
from repositories import UserRepo
from routers.reschedule.config import FRL_START_CALLBACK, ORL_RS_CALLBACK, ORL_START_CALLBACK, ORL_ONE_START_CALLBACK, router
from service import Schedule
from utils import inline_keyboard

COMMAND = "/reschedule"


class Callbacks:
    CHOOSE_CANCEL_TYPE = "rl_choose_cancel_type:"
    CHOOSE_SL_DATE_FOREVER = "rl_choose_date_sl:forever"
    CHOOSE_SL_DATE_ONCE = "rl_choose_date_sl:once"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.RESCHEDULE.value)
async def reschedule_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `/reschedule` command."""
    if not message.from_user:
        raise AiogramTelegramError
    user_id = message.from_user.id
    user = UserRepo(db).get_by_telegram_id(user_id)
    if user_id in config.BANNED_USERS or user is None:
        raise PermissionError

    await state.clear()
    await state.update_data(user_id=user.id)
    schedule = Schedule(db)
    cancellable_events = schedule.events_to_cancel(user, datetime.now(config.TIMEZONE).date())
    if not cancellable_events:
        await message.answer(replies.NO_LESSONS)
        return

    buttons = []
    for event in cancellable_events:
        if isinstance(event, Reschedule):
            buttons.append(
                (f"{event!s}", ORL_RS_CALLBACK + str(event.id)),
            )
        elif isinstance(event, Lesson):
            buttons.append(
                (f"{event!s}", ORL_ONE_START_CALLBACK + str(event.id)),
            )
        else:
            buttons.append(
                (f"{event!s}", Callbacks.CHOOSE_CANCEL_TYPE + str(event.id)),
            )

    keyboard = inline_keyboard(buttons)
    keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await message.answer(replies.CHOOSE_LESSON, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_CANCEL_TYPE))
async def reschedule_lesson_choose_cancel_type_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    lesson_id = int(callback.data.split(":")[1])  # type: ignore  # noqa: PGH003
    await state.update_data(lesson=lesson_id)
    btns = [
        (buttons.ONE_TYPE, ORL_START_CALLBACK),
        (buttons.DELETE_TYPE, FRL_START_CALLBACK),
    ]
    keyboard = inline_keyboard(btns)
    keyboard.adjust(1 if len(btns) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await callback.message.answer(replies.CANCEL_TYPE, reply_markup=keyboard.as_markup())
