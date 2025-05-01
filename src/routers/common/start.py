from aiogram import Router, html
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.orm import Session

from src.errors import AiogramTelegramError
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.repositories import UserRepo

router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def start_handler(message: Message, command: CommandObject, db: Session) -> None:
    """Handler receives messages with `/start` command."""
    if message.from_user is None:
        raise AiogramTelegramError
    tg_id, tg_full_name, tg_username = message.from_user.id, message.from_user.full_name, message.from_user.username
    user_repo = UserRepo(db)
    user = user_repo.get_by_telegram_id(tg_id)
    if user is None:
        user_repo.register(tg_id, tg_full_name, tg_username, "STUDENT", command.args)

    await message.answer(replies.GREETINGS % html.bold(tg_full_name))
    await message.answer(replies.BOT_DESCRIPTION)
