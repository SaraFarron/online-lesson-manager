from aiogram import Router, html
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import config
from logger import log_func
from middlewares import DatabaseMiddleware
from repositories import UserRepo
from service import RegistrationService

router: Router = Router()
router.message.middleware(DatabaseMiddleware())


class Messages:
    GREETINGS = "你好, %s!"
    BOT_DESCRIPTION = """
Этот бот помогает учителю планировать занятия.
Бот запомнил вас, теперь вы можете редактировать свое расписание уроков.
/help - даст клавиатуру со всеми доступными командами
"""
    WHO_ARE_YOU = "Вероятно, у вас неправильная ссылка для доступа к боту"


@router.message(CommandStart(deep_link=True))
@log_func
async def start_handler(message: Message, command: CommandObject, db: Session) -> None:
    """Handler receives messages with `/start` command."""
    tg_id, tg_full_name, tg_username = message.from_user.id, message.from_user.full_name, message.from_user.username  # type: ignore  # noqa: PGH003
    if tg_id in config.BANNED_USERS:
        return
    user_repo = UserRepo(db)
    user = user_repo.get_by_telegram_id(tg_id)
    if user is None:
        registration = RegistrationService(db)
        for code, admin_id in config.ADMINS.items():
            if command.args == code:
                registration.register(admin_id, tg_id, tg_full_name, tg_username)
                break
        else:
            await message.answer(Messages.WHO_ARE_YOU)
            return
    else:
        if not user.telegram_username:
            user.telegram_username = tg_username
        db.commit()

    await message.answer(Messages.GREETINGS % html.bold(tg_full_name))
    await message.answer(Messages.BOT_DESCRIPTION)
