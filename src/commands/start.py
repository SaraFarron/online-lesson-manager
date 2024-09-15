from aiogram import F, Router, html
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import config, logs
from database import engine
from help import Commands
from logger import log_func, logger
from models import Teacher, User

router: Router = Router()


class Messages:
    GREETINGS = "你好, %s!"
    BOT_DESCRIPTION = """
Этот бот помогает учителю планировать занятия.
Бот запомнил вас, теперь вы можете редактировать свое расписание уроков.
/help - даст клавиатуру со всеми доступными командами
"""


@router.message(CommandStart())
@router.message(F.text == Commands.START.value)
@log_func
async def start_handler(message: Message) -> None:
    """Handler receives messages with `/start` command."""
    with Session(engine) as session:
        user_is_not_registered = not session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user_is_not_registered:
            if message.from_user.id in config.ADMINS:
                teacher = Teacher(name=message.from_user.full_name, telegram_id=message.from_user.id)
                session.add(teacher)
                logger.info(logs.TEACHER_REGISTERED, message.from_user.full_name)
            teacher = session.query(Teacher).filter(Teacher.telegram_id == config.ADMINS[0]).first()
            user = User(name=message.from_user.full_name, telegram_id=message.from_user.id, teacher=teacher)
            session.add(user)
            session.commit()
            logger.info(logs.USER_REGISTERED, message.from_user.full_name)

    await message.answer(Messages.GREETINGS % html.bold(message.from_user.full_name))
    await message.answer(Messages.BOT_DESCRIPTION)
