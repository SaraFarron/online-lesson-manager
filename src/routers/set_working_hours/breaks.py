from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session
from service import Service
from config import config
from errors import AiogramTelegramError
from messages import replies
from routers.set_working_hours.config import router
from utils import inline_keyboard


class SetWorkingHoursState(StatesGroup):
    add_break = State()


@router.callback_query(F.data == "swh:edit_breaks")
async def edit_breaks(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for editing breaks."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    teacher = service.get_teacher(message.from_user.id)

    breaks = service.get_breaks(teacher)
    breaks = [
        (
            f"Убрать: {b.weekday_short_str} {b.st_str}-{b.et_str}",
            f"swh:rm_break_{b.id}",
        )
        for b in breaks
    ]

    keyboard = inline_keyboard([*breaks, ("Добавить перерыв", "swh:add_break")])
    await message.answer(replies.EDIT_BREAKS, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "swh:add_break")
async def add_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for adding breaks."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    teacher = service.get_teacher(message.from_user.id)

    keyboard = inline_keyboard(
        [
            (config.WEEKDAY_MAP[d], f"swh:add_break_{d}")
            for d in range(7)
            if d not in [w.weekday for w in teacher.weekends]
        ],
    )

    await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith("swh:add_break_"))
async def add_break_choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler for finishing adding breaks."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    service.get_teacher(message.from_user.id)

    await state.update_data(weekday=int(callback.data.replace("swh:add_break_", "")))  # type: ignore  # noqa: PGH003
    await state.set_state(SetWorkingHoursState.add_break)

    await message.answer(replies.CHOOSE_BREAK_PERIOD)


@router.message(SetWorkingHoursState.add_break)
async def add_break_finish(message: Message, state: FSMContext, db: Session) -> None:
    """Handler for finishing adding breaks."""
    if not message.from_user:
        raise AiogramTelegramError

    service = Service(db)
    teacher = service.get_teacher(message.from_user.id)
    data = await state.get_data()
    text = message.text.replace(" - ", "-") if " - " in message.text else message.text
    try:
        splitted_text = text.split("-")
        start_time = datetime.strptime(splitted_text[0], "%H:%M").time()  # noqa: DTZ007
        end_time = datetime.strptime(splitted_text[1], "%H:%M").time()  # noqa: DTZ007
    except ValueError:
        await message.answer(replies.INVALID_TIME_PERIOD)
        await state.set_state(SetWorkingHoursState.add_break)
        return

    service.create_work_break(teacher, data["weekday"], start_time, end_time)
    db.commit()

    await message.answer(replies.BREAK_ADDED)
    await state.clear()


@router.callback_query(F.data.startswith("swh:rm_break_"))
async def remove_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for removing breaks."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    teacher = service.get_teacher(message.from_user.id)
    service.delete_work_break(teacher, int(callback.data.replace("swh:rm_break_", "")))
    db.commit()

    await message.answer(replies.BREAK_REMOVED)
