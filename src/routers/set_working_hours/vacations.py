from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError, NoTextMessageError, PermissionDeniedError
from logger import log_func
from messages import replies
from models import Teacher, Vacations
from routers.set_working_hours.config import router
from utils import inline_keyboard


class States(StatesGroup):
    edit_vacation = State()
    add_vacation_start = State()
    add_vacation_end = State()
    edit_vacation_date = State()
    edit_vacation_time = State()


@router.callback_query(F.data.startswith("swh:edit_vacations"))
@log_func
async def edit_vacations_hanlder(callback: CallbackQuery, state: FSMContext, db: Session):
    """Handler for editing vacations."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    buttons = [(f"Убрать отпуск {h.start_date} - {h.end_date}", f"swh:rm_vacation_{h.id}") for h in teacher.holidays]
    buttons.append(("Добавить отпуск", "swh:add_vacation_start"))
    keyboard = inline_keyboard(buttons)
    keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await state.update_data(teacher=teacher)
    await callback.message.answer(replies.EDIT_VACATIONS, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "swh:add_vacation_start")
@log_func
async def add_vacation_start(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler for adding vacations."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    await state.set_state(States.add_vacation_start)
    await callback.message.answer(replies.CHOOSE_START)


@router.message(States.add_vacation_start)
@log_func
async def add_vacation_end(message: Message, state: FSMContext, db: Session) -> None:
    """Handler for adding vacations."""
    if not message.from_user:
        raise AiogramTelegramError
    if not message.text:
        raise NoTextMessageError
    try:
        start = datetime.strptime(message.text.split(":")[-1], "%d-%m-%Y").date()  # noqa: DTZ007
    except ValueError:
        await message.answer(replies.START_IS_AFTER_END)
        await state.clear()
        await message.answer("Операция отменена")
        return
    await state.update_data(start=start)
    teacher = db.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    await state.set_state(States.add_vacation_end)
    await message.answer(replies.CHOOSE_END)


@router.message(States.add_vacation_end)
@log_func
async def add_vacation_finish(message: Message, state: FSMContext, db: Session) -> None:
    """Handler for adding vacations."""
    if not message.from_user:
        raise AiogramTelegramError
    if not message.text:
        raise NoTextMessageError
    try:
        end = datetime.strptime(message.text.split(":")[-1], "%d-%m-%Y").date()  # noqa: DTZ007
    except ValueError:
        await message.answer(replies.INVALID_DATE)
        await state.set_state(States.add_vacation_start)
        return
    state_data = await state.get_data()
    if state_data["start"] > end:
        await message.answer(replies.START_IS_AFTER_END)
        await state.set_state(States.add_vacation_start)
        return
    teacher = db.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    vacation = Vacations(start_date=state_data["start"], end_date=end, teacher=teacher)
    db.add(vacation)
    db.commit()
    await state.clear()
    await message.answer(replies.VACATION_ADDED)


@router.callback_query(F.data.startswith("swh:rm_vacation_"))
@log_func
async def remove_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for removing breaks."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    wb = db.query(Vacations).filter(Vacations.id == int(callback.data.replace("swh:rm_vacation_", ""))).first()  # type: ignore  # noqa: PGH003
    if wb:
        db.delete(wb)
    db.commit()

    await callback.message.answer(replies.VACATION_DELETED)
