from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.orm import Session

from errors import AiogramTelegramError, NoTextMessageError, PermissionDeniedError
from help import AdminCommands
from messages import replies
from middlewares import DatabaseMiddleware
from repositories import TeacherRepo
from utils import send_message

COMMAND = "/send_to_everyone"
MAX_HOUR = 23

router = Router()
router.message.middleware(DatabaseMiddleware())


class ChooseSendToEveryone(StatesGroup):
    write_message = State()


@router.message(Command(COMMAND))
@router.message(F.text == AdminCommands.SEND_TO_EVERYONE.value)
async def send_to_everyone_handler(message: Message, state: FSMContext, db: Session) -> None:
    """First handler, gives a list of available weekdays."""
    if not message.from_user:
        raise AiogramTelegramError
    teacher = TeacherRepo(db).get_by_telegram_id(message.from_user.id)
    if teacher is None:
        raise PermissionDeniedError
    await state.set_state(ChooseSendToEveryone.write_message)
    await message.answer(replies.WRITE_MESSAGE)


@router.message(ChooseSendToEveryone.write_message)
async def send_to_everyone_write_message(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `send_to_everyone_write_message` state."""
    if not message.from_user:
        raise AiogramTelegramError
    if not message.text:
        raise NoTextMessageError
    teacher = TeacherRepo(db).get_by_telegram_id(message.from_user.id)
    if teacher is None:
        raise PermissionDeniedError
    receivers = []
    for student in teacher.students:
        receivers.append(student.username_dog)
        await send_message(student.telegram_id, message.text.replace("\n", "%0A"))
    await message.answer(replies.MESSAGES_SENT % ", ".join(receivers))
    await state.clear()
