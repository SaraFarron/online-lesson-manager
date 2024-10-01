from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.orm import Session

import messages
from database import engine
from help import AdminCommands
from logger import log_func
from models import Teacher
from utils import send_message

COMMAND = "/send_to_everyone"
MAX_HOUR = 23

router = Router()


class Messages:
    WRITE_MESSAGE = "Напишите сообщение, которое будет отправлено всем ученикам."
    MESSAGES_SENT = "Сообщения были отправлены следующим ученикам: %s"


class ChooseSendToEveryone(StatesGroup):
    write_message = State()


@router.message(Command(COMMAND))
@router.message(F.text == AdminCommands.SEND_TO_EVERYONE.value)
@log_func
async def send_to_everyone_handler(message: Message, state: FSMContext) -> None:
    """First handler, gives a list of available weekdays."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if teacher is None:
            await message.answer(messages.PERMISSION_DENIED)
            return
    await state.set_state(ChooseSendToEveryone.write_message)
    await message.answer(Messages.WRITE_MESSAGE)


@router.message(ChooseSendToEveryone.write_message)
@log_func
async def send_to_everyone_write_message(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `send_to_everyone_write_message` state."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if teacher is None:
            await message.answer(messages.PERMISSION_DENIED)
            return
        receivers = []
        for student in teacher.students:
            receivers.append(student.username_dog)
            await send_message(student.telegram_id, message.text)
    await message.answer(Messages.MESSAGES_SENT % ", ".join(receivers))
    await state.clear()
