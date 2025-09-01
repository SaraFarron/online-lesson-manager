from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.db.models import RecurrentEvent
from src.db.repositories import EventHistoryRepo
from src.db.schemas import RolesSchema
from src.keyboards import AdminCommands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.services import EventService, UserService
from src.utils import get_callback_arg, parse_time

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class WorkBreaks(StatesGroup):
    scene = "manage_work_breaks"
    command = "/" + scene
    base_callback = scene + "/"
    add_break = f"{base_callback}add_break/"
    remove_break = f"{base_callback}remove_break/"
    choose_duration = f"{base_callback}choose_duration/"
    result = State()


@router.message(Command(WorkBreaks.command))
@router.message(F.text == AdminCommands.WORK_BREAKS.value)
async def manage_work_breaks_handler(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user(message, RolesSchema.TEACHER)

    await state.update_data(user_id=user.telegram_id)
    work_breaks = EventService(db).work_breaks(user.executor_id)
    await message.answer(
        replies.CHOOSE_WH_ACTION,
        reply_markup=Keyboards.work_breaks(work_breaks, WorkBreaks.add_break, WorkBreaks.remove_break),
    )


@router.callback_query(F.data.startswith(WorkBreaks.add_break))
async def add_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, _ = UserService(db).check_user_with_id(callback, state_data["user_id"], RolesSchema.TEACHER)

    await message.answer(
        replies.CHOOSE_WEEKDAY, reply_markup=Keyboards.weekdays(list(range(7)), WorkBreaks.choose_duration),
    )


@router.callback_query(F.data.startswith(WorkBreaks.choose_duration))
async def choose_duration(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, _ = UserService(db).check_user_with_id(callback, state_data["user_id"], RolesSchema.TEACHER)

    weekday = get_callback_arg(callback.data, WorkBreaks.choose_duration)
    await state.update_data(weekday=weekday)

    await message.answer(replies.CHOOSE_TIMES)
    await state.set_state(WorkBreaks.result)


@router.message(WorkBreaks.result)
async def result(message: Message, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(message, state_data["user_id"], RolesSchema.TEACHER)

    user_input = message.text.split("-")
    if len(user_input) != 2:
        await message.answer(replies.WRONG_TIMES_FMT)
        await state.set_state(WorkBreaks.result)
        return
    start, end = parse_time(user_input[0].strip()), parse_time(user_input[1].strip())
    if start > end:
        await message.answer(replies.START_LT_END)
        await state.set_state(WorkBreaks.result)
        return

    now = datetime.now()
    weekday = int(state_data["weekday"])
    start_of_week = now - timedelta(days=now.weekday())
    day = start_of_week + timedelta(days=weekday)
    event = RecurrentEvent(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=RecurrentEvent.EventTypes.WORK_BREAK,
        start=day.replace(hour=start.hour, minute=start.minute),
        end=day.replace(hour=end.hour, minute=end.minute),
        interval=7,
    )
    db.add(event)
    db.commit()
    await message.answer(replies.BREAK_ADDED)
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(username, WorkBreaks.scene, "added_break", f"{weekday} {start.time()}")
    await state.clear()


@router.callback_query(F.data.startswith(WorkBreaks.remove_break))
async def remove_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"], RolesSchema.TEACHER)

    event_id = get_callback_arg(callback.data, WorkBreaks.remove_break)
    event = db.get(RecurrentEvent, int(event_id))
    event_str = f"{event.start.weekday()} {event.start.time()}"
    db.delete(event)
    db.commit()

    await message.answer(replies.BREAK_REMOVED)
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(username, WorkBreaks.scene, "removed_break", event_str)
    await state.clear()
