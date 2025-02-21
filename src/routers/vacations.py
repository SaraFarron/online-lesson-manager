from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session
from service import Service
from errors import AiogramTelegramError, NoTextMessageError
from help import Commands
from messages import replies
from middlewares import DatabaseMiddleware
from utils import inline_keyboard

COMMAND = "/edit_vacations"

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())


class States(StatesGroup):
    edit_vacation = State()
    add_vacation_start = State()
    add_vacation_end = State()
    edit_vacation_date = State()
    edit_vacation_time = State()


@router.message(Command(COMMAND))
@router.message(F.text == Commands.VACATIONS.value)
async def vacations_hanlder(message: Message, state: FSMContext, db: Session):
    """Handler for editing vacations."""
    if not isinstance(message, Message):
        raise AiogramTelegramError

    service = Service(db)
    user = service.get_user(message.from_user.id)
    buttons = [(f"Убрать каникулы {h.start_date} - {h.end_date}", f"vacations:rm_v_{h.id}") for h in
               user.holidays]
    buttons.append(("Добавить каникулы", "vacations:add_vacation_start"))
    keyboard = inline_keyboard(buttons)

    await state.update_data(user=user)
    await message.answer(replies.EDIT_VACATIONS, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "vacations:add_vacation_start")
async def add_vacation_start(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler for adding vacations."""
    message = callback.message
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError

    service = Service(db)
    service.get_user(message.from_user.id)

    await state.set_state(States.add_vacation_start)
    await message.answer(replies.CHOOSE_START)


@router.message(States.add_vacation_start)
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
        await state.set_state(States.add_vacation_start)
        return
    await state.update_data(start=start)

    service = Service(db)
    service.get_user(message.from_user.id)

    await state.set_state(States.add_vacation_end)
    await message.answer(replies.CHOOSE_END)


@router.message(States.add_vacation_end)
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

    service = Service(db)
    user = service.get_user(message.from_user.id)
    vacation = service.create_vacations(user, state_data["start"], end)
    db.commit()
    await message.answer(replies.VACATION_ADDED)
    await state.clear()


@router.callback_query(F.data.startswith("vacations:rm_v_"))
async def remove_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for removing breaks."""
    message = callback.message
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError

    service = Service(db)
    user = service.get_user(message.from_user.id)
    v_id = int(callback.data.replace("vacations:rm_v_", ""))
    service.delete_vacation(user, v_id)
    db.commit()

    await message.answer(replies.VACATION_DELETED)
