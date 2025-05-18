
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.keyboards import AdminCommands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import User
from src.repositories import EventHistoryRepo, UserRepo
from src.utils import get_callback_arg, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class Profile(StatesGroup):
    scene = "profiles"
    command = "/" + scene
    base_callback = scene + "/"
    profile = f"{base_callback}profile/"
    delete_student = f"{base_callback}delete/"


@router.message(Command(Profile.command))
@router.message(F.text == AdminCommands.STUDENTS.value)
async def profile_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    await state.update_data(user_id=user.telegram_id)

    students = list(db.query(User).filter(User.executor_id == user.executor_id))
    await message.answer(replies.CHOOSE_ACTION, Keyboards.users(students, Profile.profile))


@router.callback_query(F.data.startswith(Profile.profile))
async def profile(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    student_id = int(get_callback_arg(callback.data, Profile.profile))
    student = db.get(User, student_id)
    if student is None:
        raise Exception("message", "Пользователь не найден", f"user not found: {student_id}")
    event_history = EventHistoryRepo(db).user_history(student.username)
    profile_text = f"""
    Telegram id: {student.telegram_id}
    Telegram username: {student.username}
    Имя: {student.full_name}
    История последних 10 действий:
    """ + "\n".join([f"{e.created_at} {e.event_type} {e.event_value}" for e in event_history])
    await message.answer(profile_text, Keyboards.profile(student_id, Profile.delete_student))

# TODO