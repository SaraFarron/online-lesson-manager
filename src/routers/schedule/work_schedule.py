from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core.config import WEEKDAY_MAP
from src.db.models import RecurrentEvent, User
from src.db.repositories import EventHistoryRepo
from src.keyboards import AdminCommands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.services import EventRepo, UserService
from src.utils import get_callback_arg, parse_time, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class WorkSchedule(StatesGroup):
    scene = "manage_work_schedule"
    command = "/" + scene
    base_callback = scene + "/"
    action = f"{base_callback}action/"
    choose_time = State()
    choose_weekday = f"{base_callback}choose_weekday/"
    create_weekend = f"{base_callback}create_weekend/"


@router.message(Command(WorkSchedule.command))
@router.message(F.text == AdminCommands.MANAGE_WORK_HOURS.value)
async def manage_work_schedule_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserService(db).get_by_telegram_id(message.from_user.id, True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    await state.update_data(user_id=user.telegram_id)
    work_hours = EventRepo(db).work_hours(user.executor_id)
    weekends = EventRepo(db).weekends(user.executor_id)
    await message.answer(
        replies.CHOOSE_WH_ACTION,
        reply_markup=Keyboards.work_hours(work_hours, weekends, WorkSchedule.action, WorkSchedule.choose_weekday),
    )

# ---- WORK HOURS ---- #

@router.callback_query(F.data.startswith(WorkSchedule.action))
async def action(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    action_type = callback.data.split("/")[-1]
    username = user.username if user.username else user.full_name
    if action_type.startswith("delete"):
        if action_type.endswith("start"):
            time = EventRepo(db).delete_work_hour_setting(user.executor_id, "start")
            EventHistoryRepo(db).create(username, WorkSchedule.scene, "deleted_start", str(time))
        elif action_type.endswith("end"):
            time = EventRepo(db).delete_work_hour_setting(user.executor_id, "end")
            EventHistoryRepo(db).create(username, WorkSchedule.scene, "deleted_end", str(time))
        await message.answer(replies.WORK_HOURS_DELETED)
    elif action_type.startswith("add"):
        if action_type.endswith("start"):
            await state.update_data(mode="start")
        elif action_type.endswith("end"):
            await state.update_data(mode="end")
        await state.set_state(WorkSchedule.choose_time)
        await message.answer(replies.CHOOSE_TIME)
    else:
        raise Exception("message", "Неизвестное действие", f"callback.data is unknown: {callback.data}")


@router.message(WorkSchedule.choose_time)
async def choose_time(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    time = parse_time(message.text).time()
    current_day = datetime.now().date()
    start = datetime.combine(current_day, time)
    if state_data["mode"] == "start":
        event_type = RecurrentEvent.EventTypes.WORK_START
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=time.hour, minute=time.minute)
    elif state_data["mode"] == "end":
        event_type = RecurrentEvent.EventTypes.WORK_END
        end = datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)
        start = end.replace(hour=time.hour, minute=time.minute)
    event = RecurrentEvent(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=event_type,
        start=start,
        end=end,
        interval=1,
    )
    db.add(event)
    db.commit()
    await message.answer(replies.WH_CHANGED)
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(username, WorkSchedule.scene, f"added_{state_data['mode']}", str(event))
    await state.clear()

# ---- WEEKENDS ---- #

@router.callback_query(F.data.startswith(WorkSchedule.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    event_id = get_callback_arg(callback.data, WorkSchedule.choose_weekday)
    if "delete_weekend" in callback.data:
        event_id = int(event_id.replace("delete_weekend/", ""))
        event = db.get(RecurrentEvent, event_id)
        weekday = WEEKDAY_MAP[event.start.weekday()]["short"]
        db.delete(event)
        db.commit()
        await message.answer(replies.WEEKEND_DELETED)
        await state.clear()
        username = user.username if user.username else user.full_name
        EventHistoryRepo(db).create(username, WorkSchedule.scene, "deleted_weekend", weekday)
    elif "add_weekend" in callback.data:
        weekdays = EventRepo(db).available_work_weekdays(user.executor_id)
        await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=Keyboards.weekdays(weekdays, WorkSchedule.create_weekend))
    else:
        raise Exception("message", "Неизвестное событие", f"unknown weekend action {callback.data}")


@router.callback_query(F.data.startswith(WorkSchedule.create_weekend))
async def create_weekend(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    weekday = int(get_callback_arg(callback.data, WorkSchedule.create_weekend))
    start_of_week = datetime.now() - timedelta(days=datetime.now().weekday())
    day = start_of_week + timedelta(days=weekday)
    event = RecurrentEvent(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=RecurrentEvent.EventTypes.WEEKEND,
        start=day.replace(hour=0, minute=0),
        end=day.replace(hour=23, minute=59),
        interval=7,
    )
    db.add(event)
    db.commit()
    await message.answer(replies.WEEKEND_ADDED)
    weekday = WEEKDAY_MAP[weekday]["short"]
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(username, WorkSchedule.scene, "added_weekend", weekday)
    await state.clear()
