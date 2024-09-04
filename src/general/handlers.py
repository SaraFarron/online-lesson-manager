from aiogram import F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.orm import Session

from config import help, logs, messages
from config.messages import BOT_DESCRIPTION, HELP_MESSAGE
from database import engine
from general.keyboards import all_commands_keyboard
from logger import logger, log_func
from models import User

router: Router = Router()


@router.message(Command("help"))
@router.message(F.text == help.HELP)
@log_func
async def get_help(message: Message) -> None:
    """Handler receives messages with `/help` command."""
    await message.answer(HELP_MESSAGE, reply_markup=all_commands_keyboard(message.from_user.id))


@router.message(CommandStart())
@router.message(F.text == help.START)
@log_func
async def command_start_handler(message: Message) -> None:
    """Handler receives messages with `/start` command."""
    with Session(engine) as session:
        if not session.query(User).filter(User.telegram_id == message.from_user.id).first():
            user = User(name=message.from_user.full_name, telegram_id=message.from_user.id)
            session.add(user)
            session.commit()
            logger.info(logs.USER_REGISTERED, message.from_user.full_name)
    await message.answer(messages.GREETINGS % html.bold(message.from_user.full_name))
    await message.answer(BOT_DESCRIPTION)


@router.message(Command("cancel"))
@router.message(F.text == help.CANCEL)
@log_func
async def cancel_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/cancel` command."""
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.clear()
    await message.answer(messages.CANCELED)  # , reply_markup=ReplyKeyboardRemove()
