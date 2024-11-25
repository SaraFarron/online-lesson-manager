from aiogram import F, Router, html
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import config, logs
from help import Commands
from logger import log_func, logger
from middlewares import DatabaseMiddleware
from repositories import TeacherRepo, UserRepo
from utils import get_teacher

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
    pasha_tid = config.ADMINS[0]
    user_repo, teacher_repo = UserRepo(db), TeacherRepo(db)
    user = user_repo.get_by_telegram_id(tg_id)
    if user is None:
        if command.args != config.INVITE_CODE:
            await message.answer(Messages.WHO_ARE_YOU)
            return
        if tg_id in config.ADMINS:
            teacher = teacher_repo.register(tg_full_name, tg_id, tg_username if tg_username else tg_full_name)
            logger.info(logs.TEACHER_REGISTERED, tg_full_name)
            db.commit()
        elif command.args == config.INVITE_CODE:
            pasha = teacher_repo.get_by_telegram_id(pasha_tid)
            if not pasha:
                msg = "Pasha not found"
                raise ValueError(msg)
            user_repo.new(tg_full_name, tg_id, pasha, tg_username)
            db.commit()
        else:
            teacher = get_teacher(db)
        logger.info(logs.USER_REGISTERED, tg_full_name)
    else:
        if not user.telegram_username:
            user.telegram_username = tg_username
        if user.teacher.telegram_id != pasha_tid:
            teacher = get_teacher(db)
            if not teacher:
                msg = "Teacher not found"
                raise ValueError(msg)
            user.teacher = teacher
            user.teacher_id = teacher.id
        db.commit()

    await message.answer(Messages.GREETINGS % html.bold(tg_full_name))
    await message.answer(Messages.BOT_DESCRIPTION)
