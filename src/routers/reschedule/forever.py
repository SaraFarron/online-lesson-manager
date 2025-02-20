from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError
from messages import buttons, replies
from routers.reschedule.config import FRL_START_CALLBACK, router
from utils import inline_keyboard


class Callbacks:
    CHOOSE_WEEKDAY = "frl_choose_weekday:"
    CONFIRM = "frl_confirm:"
    CHOOSE_DATE = "frl_choose_date:"
    CHOOSE_TIME = "frl_choose_time:"


@router.callback_query(F.data.startswith(FRL_START_CALLBACK))
async def frl_cancel_or_reschedule(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    service.get_user(message.from_user.id)

    keyboard = inline_keyboard(
        [
            (buttons.CANCEL_LESSON, Callbacks.CONFIRM),
            (buttons.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
        ],
    ).as_markup()

    await message.answer(replies.CONFIRM, reply_markup=keyboard)


@router.callback_query(F.data == Callbacks.CONFIRM)
async def frl_delete_sl(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_confirm` state."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    user = service.get_user(message.from_user.id)

    state_data = await state.get_data()
    sl = service.get_sl(user, state_data["lesson"])

    if not service.sl_is_cancellable(user, sl):
        await message.answer(f"{replies.CHOOSE_REASONABLE_TIME}. {replies.ACTION_CANCELLED}")
        await state.clear()
        return

    service.delete_sl(user, sl)
    db.commit()

    await state.clear()
    await message.answer(replies.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
async def frl_choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    user = service.get_user(message.from_user.id)

    weekdays = service.available_weekdays(user)
    keyboard = inline_keyboard(weekdays)

    await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
async def frl_choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_choose_time` state."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    user = service.get_user(message.from_user.id)

    weekday = int(callback.data.split(":")[1])  # type: ignore  # noqa: PGH003
    if weekday not in service.available_weekdays(user):
        await callback.message.answer(replies.WRONG_WEEKDAY % config.WEEKDAY_MAP_FULL[weekday])
        return
    await state.update_data(new_date=weekday)
    available_time = service.available_time(user, weekday)
    buttons = [(t.strftime("%H:%M"), Callbacks.CHOOSE_TIME + t.strftime("%H.%M")) for t in available_time]
    keyboard = inline_keyboard(buttons)

    await message.answer(replies.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
async def frl_update_sl(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_create_reschedule` state."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    user = service.get_user(message.from_user.id)

    state_data = await state.get_data()
    time = datetime.strptime(callback.data.split(":")[1], "%H.%M").time()  # type: ignore  # noqa: PGH003, DTZ007
    service.move_sl(user, state_data["lesson"], time)
    db.commit()

    await state.clear()
    await message.answer(replies.LESSON_ADDED)
