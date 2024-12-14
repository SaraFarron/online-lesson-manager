from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from errors import AiogramTelegramError, NoTextMessageError, PermissionDeniedError
from logger import log_func
from messages import replies
from models import Teacher
from routers.set_working_hours.config import router


class SetWorkingHoursState(StatesGroup):
    edit_work_hours = State()


@router.callback_query(F.data == "swh:start")
@log_func
async def set_work_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for changing the start of the work day."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    await state.update_data(scene="start")
    await state.set_state(SetWorkingHoursState.edit_work_hours)
    await callback.message.answer(replies.SET_TIME)


@router.callback_query(F.data == "swh:end")
@log_func
async def set_work_end(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler for changing the end of the work day."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    await state.update_data(scene="end")
    await state.set_state(SetWorkingHoursState.edit_work_hours)
    await callback.message.answer(replies.SET_TIME)


@router.message(SetWorkingHoursState.edit_work_hours)
@log_func
async def set_work_border_handler(message: Message, state: FSMContext, db: Session) -> None:
    """Handler for changing the start of the work day."""
    if not message.text:
        raise NoTextMessageError
    if not message.from_user:
        raise AiogramTelegramError
    try:
        time = datetime.strptime(message.text, "%H:%M").time()  # noqa: DTZ007
    except ValueError:
        await state.set_state(SetWorkingHoursState.edit_work_hours)
        await message.answer(replies.WRONG_TIME_FORMAT)
        return
    state_data = await state.get_data()
    teacher = db.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    if state_data["scene"] == "start":
        teacher.work_start = time
    elif state_data["scene"] == "end":
        teacher.work_end = time
    else:
        await message.answer(replies.ERROR)
        return
    db.commit()
    await state.clear()
    await message.answer(replies.TIME_UPDATED)
