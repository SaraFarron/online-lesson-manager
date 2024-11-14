from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.orm import Session

import messages
from help import AdminCommands
from logger import log_func
from middlewares import DatabaseMiddleware
from repositories import TeacherRepo
from utils import send_message

COMMAND = "/send_to_everyone"
MAX_HOUR = 23

router = Router()
router.message.middleware(DatabaseMiddleware())


class Messages:
    WRITE_MESSAGE = "Напишите сообщение, которое будет отправлено всем ученикам."
    MESSAGES_SENT = "Сообщения были отправлены следующим ученикам: %s"


class ChooseSendToEveryone(StatesGroup):
    write_message = State()


@router.message(Command(COMMAND))
@router.message(F.text == AdminCommands.SEND_TO_EVERYONE.value)
@log_func
async def send_to_everyone_handler(message: Message, state: FSMContext, db: Session) -> None:
    """First handler, gives a list of available weekdays."""
    teacher = TeacherRepo(db).get_by_telegram_id(message.from_user.id)  # type: ignore  # noqa: PGH003
    if teacher is None:
        await message.answer(messages.PERMISSION_DENIED)
        return
    await state.set_state(ChooseSendToEveryone.write_message)
    await message.answer(Messages.WRITE_MESSAGE)


@router.message(ChooseSendToEveryone.write_message)
@log_func
async def send_to_everyone_write_message(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `send_to_everyone_write_message` state."""
    teacher = TeacherRepo(db).get_by_telegram_id(message.from_user.id)  # type: ignore  # noqa: PGH003
    if teacher is None:
        await message.answer(messages.PERMISSION_DENIED)
        return
    receivers = []
    for student in teacher.students:
        receivers.append(student.username_dog)
        await send_message(student.telegram_id, message.text.replace("\n", "%0A"))  # type: ignore  # noqa: PGH003
    await message.answer(Messages.MESSAGES_SENT % ", ".join(receivers))
    await state.clear()
