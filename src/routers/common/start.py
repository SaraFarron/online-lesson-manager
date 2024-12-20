from aiogram import Router, html
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError, PermissionDeniedError
from messages import replies
from middlewares import DatabaseMiddleware
from repositories import UserRepo
from service import RegistrationService

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
    user_repo = UserRepo(db)
    user = user_repo.get_by_telegram_id(tg_id)
    if user is None:
        registration = RegistrationService(db)
        for code, admin_id in config.ADMINS.items():
            if command.args == code:
                registration.register(admin_id, tg_id, tg_full_name, tg_username)
                break
        else:
            raise PermissionDeniedError
    else:
        if not user.telegram_username:
            user.telegram_username = tg_username
        db.commit()

    await message.answer(replies.GREETINGS % html.bold(tg_full_name))
    await message.answer(replies.BOT_DESCRIPTION)
