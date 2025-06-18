from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.config import DATE_FMT, DATETIME_FMT, DB_DATETIME
from src.db.models import Event, User
from src.db.repositories import EventHistoryRepo
from src.db.schemas import RolesSchema
from src.keyboards import AdminCommands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.services import HISTORY_MAP, UserService
from src.utils import get_callback_arg, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())


def profile_text(tg_id: int, username: str, fullname: str, events: list, vacations: list):
    vacations_list = []
    for vacation in vacations:
        start, end = datetime.strptime(vacation[0], DB_DATETIME), datetime.strptime(vacation[1], DB_DATETIME)
        start, end = datetime.strftime(start, DATE_FMT), datetime.strftime(end, DATE_FMT)
        vacations_list.append(f"{start} - {end}")
    if vacations_list:
        vac_text = "\n".join(["Каникулы:"] + vacations_list)
    else:
        vac_text = "Каникул нет"
    link = f"@{username}" if username else f'<a href="tg://user?id={tg_id}">{fullname}</a>'
    return f"""
Telegram id: {tg_id}
Telegram username: {username}
Ссылка: {link}
Имя: {fullname}
{vac_text}

История последних 10 действий:

""" + "\n".join(events)

class Profile(StatesGroup):
    scene = "profiles"
    command = "/" + scene
    base_callback = scene + "/"
    profile = f"{base_callback}profile/"
    delete_student = f"{base_callback}delete/"
    confirm = f"{base_callback}confirm/"


@router.message(Command(Profile.command))
@router.message(F.text == AdminCommands.STUDENTS.value)
async def profile_handler(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user(message, RolesSchema.TEACHER)
    await state.update_data(user_id=user.telegram_id)

    students = list(db.query(User).filter(User.executor_id == user.executor_id, User.telegram_id != user.telegram_id))
    await message.answer(replies.CHOOSE_ACTION, reply_markup=Keyboards.users(students, Profile.profile))


@router.callback_query(F.data.startswith(Profile.profile))
async def profile(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    student_id = int(get_callback_arg(callback.data, Profile.profile))
    student = db.get(User, student_id)
    if student is None:
        raise Exception("message", "Пользователь не найден", f"user not found: {student_id}")
    event_history = EventHistoryRepo(db).user_history(student.username)
    events = []
    for e in event_history:
        dt = datetime.strptime(e.created_at, DB_DATETIME)
        event = HISTORY_MAP[e.event_type] if e.event_type in HISTORY_MAP else e.event_type
        events.append(f"{datetime.strftime(dt, DATETIME_FMT)} {event} {e.event_value}")
    vacations = list(db.execute(text("""
        select start, end from events
        where user_id = :user_id and event_type == :event_type and start >= :today
    """), {"user_id": student.id, "event_type": Event.EventTypes.VACATION, "today": datetime.now()}).fetchall())
    msg = profile_text(student.telegram_id, student.username, student.full_name, events, vacations)
    await message.answer(msg, reply_markup=Keyboards.profile(student_id, Profile.delete_student))


@router.callback_query(F.data.startswith(Profile.delete_student))
async def delete_student(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    student_id = int(get_callback_arg(callback.data, Profile.delete_student))
    await state.update_data(student_id=student_id)
    await message.answer(replies.ARE_YOU_SURE, reply_markup=Keyboards.confirm(Profile.confirm))


@router.callback_query(F.data.startswith(Profile.confirm))
async def confirm(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    answer = get_callback_arg(callback.data, Profile.confirm)
    if answer != "yes":
        await message.answer(replies.CANCELED)
        await state.clear()
        return

    student_id = state_data["student_id"]
    UserService(db).delete(student_id)
    await message.answer(replies.USER_DELETED)
    await state.clear()
    EventHistoryRepo(db).create(user.usernmae, Profile.scene, "deleted_user", str(student_id))
