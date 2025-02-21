from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session
from service import Service
from errors import AiogramTelegramError
from help import Commands
from middlewares import DatabaseMiddleware

COMMAND = "today_schedule"
router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message(Command(COMMAND))
@router.message(F.text == Commands.TODAY_SCHEDULE.value)
async def today_schedule_handler(message: Message, db: Session) -> None:
    """Handler returns today's schedule."""
    t_user = message.from_user
    if not t_user:
        raise AiogramTelegramError

    service = Service(db)
    user = service.get_user(message.from_user.id)
    lessons = service.get_lessons(user)

    await message.answer(lessons)
