from __future__ import annotations

from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

import messages
from commands.set_working_hours.config import router
from config import config
from database import engine
from help import AdminCommands
from logger import log_func
from models import Teacher
from utils import inline_keyboard

COMMAND = "/reschedule"


@router.message(Command(COMMAND))
@router.message(F.text == AdminCommands.EDIT_WORKING_HOURS.value)
@log_func
async def set_working_hours_handler(message: Message) -> None:
    """Handler receives messages with `/reschedule` command."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
        if not teacher:
            await message.answer(messages.PERMISSION_DENIED)
            return
        buttons = [
            (f"Изменить начало рабочего дня: {teacher.work_start.strftime('%H:%M')}", "swh:start"),
            (f"Изменить конец рабочего дня: {teacher.work_end.strftime('%H:%M')}", "swh:end"),
            *[
                (f"Убрать выходной: {config.WEEKDAY_MAP_FULL[weekend.weekday]}", f"swh:rm_weekend_{weekend.id}")
                for weekend in teacher.weekends
            ],
            ("Добавить выходной", "swh:add_weekend"),
            ("Изменить перерывы", "swh:edit_breaks"),
            ("Изменить отпуска", "swh:edit_vacations"),
        ]
        keyboard = inline_keyboard(buttons)
        keyboard.adjust(1 if len(buttons) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
        await message.answer("Выберите действие", reply_markup=keyboard.as_markup())
