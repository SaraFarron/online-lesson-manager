from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError, NoTextMessageError, PermissionDeniedError
from help import Commands
from messages import replies
from middlewares import DatabaseMiddleware
from models import Vacations
from repositories import UserRepo, VacationsRepo
from utils import inline_keyboard, send_message

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
    await state.clear()

    user = UserRepo(db).get_by_telegram_id(message.from_user.id)
    if user is None:
        raise PermissionDeniedError
    buttons = [(f"Убрать каникулы {h.start_date} - {h.end_date}", f"vacations:rm_v_{h.id}") for h in
               user.holidays]
    buttons.append(("Добавить каникулы", "vacations:add_vacation_start"))
    keyboard = inline_keyboard(buttons)
    keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await state.update_data(user=user)
    await message.answer(replies.EDIT_VACATIONS, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "vacations:add_vacation_start")
async def add_vacation_start(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler for adding vacations."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    user = UserRepo(db).get_by_telegram_id(callback.from_user.id)
    if user is None:
        raise PermissionDeniedError
    await state.set_state(States.add_vacation_start)
    await callback.message.answer(replies.CHOOSE_START)


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
        await state.clear()
        await message.answer("Операция отменена")
        return
    await state.update_data(start=start)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id)
    if user is None:
        raise PermissionDeniedError
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
    user = UserRepo(db).get_by_telegram_id(message.from_user.id)
    if user is None:
        raise PermissionDeniedError
    vacation = VacationsRepo(db).new(user, state_data["start"], end)
    db.commit()
    await state.clear()
    await message.answer(replies.VACATION_ADDED)
    await send_message(
        user.teacher.telegram_id,
       replies.USER_ADDED_VACATION % (user.username_dog, vacation.start_date, vacation.end_date),
    )


@router.callback_query(F.data.startswith("vacations:rm_v_"))
async def remove_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for removing breaks."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    user = UserRepo(db).get_by_telegram_id(callback.from_user.id)
    if user is None:
        raise PermissionDeniedError
    v_id = int(callback.data.replace("vacations:rm_v_", ""))
    VacationsRepo(db).delete(Vacations.id == v_id)
    db.commit()

    await callback.message.answer(replies.VACATION_DELETED)
    await send_message(user.teacher.telegram_id, replies.USER_DELETED_VACATION % user.username_dog)
