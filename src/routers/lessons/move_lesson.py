from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core import config
from src.core.help import Commands
from src.keyboards import Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import Event
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import get_callback_arg, parse_date, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class MoveLesson(StatesGroup):
    scene = "move_lesson"
    command = "/" + scene
    base_callback = scene + "/"
    choose_lesson = f"{base_callback}choose_lesson/"
    move_or_delete = f"{base_callback}move_or_delete/"
    type_date = State()
    choose_time = f"{base_callback}move/choose_time/"


@router.message(Command(MoveLesson.command))
@router.message(F.text == Commands.MOVE_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)

    await state.update_data(user_id=user.telegram_id)
    lessons = EventRepo(db).all_user_lessons(user)
    if lessons:
        await message.answer(replies.CHOOSE_LESSON, reply_markup=Keyboards.choose_lesson(lessons, MoveLesson.choose_lesson))
    else:
        await message.answer(replies.NO_LESSONS)


@router.callback_query(F.data.startswith(MoveLesson.choose_lesson))
async def choose_lesson(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    await state.update_data(lesson=get_callback_arg(callback.data, MoveLesson.choose_lesson))
    await message.answer(replies.MOVE_OR_DELETE, reply_markup=Keyboards.move_or_delete(MoveLesson.move_or_delete))


@router.callback_query(F.data.startswith(MoveLesson.move_or_delete))
async def move_or_delete(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    action = get_callback_arg(callback.data, MoveLesson.move_or_delete)
    if action == "delete" and state_data["lesson"].startswith("e"):
        lesson = EventRepo(db).cancel_event(int(state_data["lesson"].replace("e", "")))
        EventHistoryRepo(db).create(user.username, MoveLesson.scene, "deleted_one_lesson", str(lesson))
        await state.clear()
        await message.answer(replies.LESSON_DELETED)
        return
    if action == "delete" and state_data["lesson"].startswith("re"):
        pass
    elif action == "move" and state_data["lesson"].startswith("e"):
        await state.set_state(MoveLesson.type_date)
        await message.answer(replies.CHOOSE_LESSON_DATE)
    elif action == "move" and state_data["lesson"].startswith("re"):
        pass
    else:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        print(action)
        await state.clear()
    # TODO

# ---- MOVE ONE LESSON ---- #

@router.message(MoveLesson.type_date)
async def type_date(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    day = parse_date(message.text)
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(MoveLesson.type_date)
        return
    day = day.date()
    today = datetime.now().date()
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        await state.set_state(MoveLesson.type_date)
        return

    await state.update_data(day=day)
    available_time = EventRepo(db).available_time(user.executor_id, day)
    available_time = [s for s, e in available_time]
    await message.answer(replies.CHOOSE_TIME, reply_markup=Keyboards.choose_time(available_time, MoveLesson.choose_time))


@router.callback_query(F.data.startswith(MoveLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    day = state_data["day"]
    time = datetime.strptime(
        get_callback_arg(callback.data, MoveLesson.choose_time),
        config.TIME_FMT,
    ).time()

    old_lesson = EventRepo(db).cancel_event(int(state_data["lesson"].replace("e", "")))
    new_lesson = Event(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=Event.EventTypes.LESSON,
        start=datetime.combine(day, time),
        end=datetime.combine(day, time.replace(hour=time.hour + 1)),
    )
    db.add(new_lesson)
    db.commit()
    await message.answer(replies.LESSON_MOVED)
    EventHistoryRepo(db).create(
        user.username, MoveLesson.scene, "moved_one_lesson", f"{old_lesson} -> {new_lesson}"
    )
    await state.clear()
