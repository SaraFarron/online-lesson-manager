from aiogram import Router, html
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError, PermissionDeniedError
from messages import replies
from middlewares import DatabaseMiddleware

router: Router = Router()
router.message.middleware(DatabaseMiddleware())


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def start_handler(message: Message, command: CommandObject, db: Session) -> None:
    """Handler receives messages with `/start` command."""
    if message.from_user is None:
        raise AiogramTelegramError
    tg_id, tg_full_name, tg_username = message.from_user.id, message.from_user.full_name, message.from_user.username
    if tg_id in config.BANNED_USERS:
        raise PermissionDeniedError

    new_user = Service(db).register(t_id, tg_full_name, tg_username)
    if new_user:
        db.commit()

    await message.answer(replies.GREETINGS % html.bold(tg_full_name))
    await message.answer(replies.BOT_DESCRIPTION)
