from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from log import log_func
from middlewares import DatabaseMiddleware
from repositories import UserRepo

router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message(CommandStart())
@log_func
async def start_handler(message: Message, db: AsyncSession) -> None:
    """Handler receives messages with `/start` command."""
    if message.from_user is None:
        return
    await UserRepo(db).new(
        user_id=message.from_user.id,
        user_name=message.from_user.username,
        first_name=message.from_user.first_name,
        second_name=message.from_user.last_name,
    )
    await db.commit()
    await message.answer(f"Hello, {message.from_user.username}!")
