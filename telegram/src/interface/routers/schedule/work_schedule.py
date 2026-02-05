from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core.config import WEEKDAY_MAP
from src.core.middlewares import DatabaseMiddleware
from src.db.models import RecurrentEvent
from src.db.repositories import EventHistoryRepo
from src.db.schemas import RolesSchema
from src.interface.keyboards import AdminCommands, Keyboards
from src.interface.messages import replies
from src.service.services import EventService, UserService
from src.service.utils import get_callback_arg, parse_time

router = Router()


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
    message, user = UserService(db).check_user(message, RolesSchema.TEACHER)

    await state.update_data(user_id=user.telegram_id)
    work_hours, weekends = EventService(db).work_schedule(user.executor_id)
    await message.answer(
        replies.CHOOSE_WH_ACTION,
        reply_markup=Keyboards.work_hours(work_hours, weekends, WorkSchedule.action, WorkSchedule.choose_weekday),
    )

# ---- WORK HOURS ---- #

@router.callback_query(F.data.startswith(WorkSchedule.action))
async def action(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

    action_type = callback.data.split("/")[-1]
    username = user.username if user.username else user.full_name
    if action_type.startswith("delete"):
        if action_type.endswith("start"):
            time = EventService(db).delete_work_hour(user.executor_id, "start")
            EventHistoryRepo(db).create(username, WorkSchedule.scene, "deleted_start", str(time))
        elif action_type.endswith("end"):
            time = EventService(db).delete_work_hour(user.executor_id, "end")
            EventHistoryRepo(db).create(username, WorkSchedule.scene, "deleted_end", str(time))
        await message.answer(replies.WORK_HOURS_DELETED)
    elif action_type.startswith("add"):
        if action_type.endswith("start"):
            await state.update_data(mode="start")
        elif action_type.endswith("end"):
            await state.update_data(mode="end")
        await state.set_state(WorkSchedule.choose_time)
        await message.answer(replies.INPUT_TIME)
    else:
        raise Exception("message", "Неизвестное действие", f"callback.data is unknown: {callback.data}")


@router.message(WorkSchedule.choose_time)
async def choose_time(message: Message, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(message, state_data["user_id"], RolesSchema.TEACHER)

    now = datetime.now()
    time = parse_time(message.text).time()
    current_day = now.date()
    start = datetime.combine(current_day, time)
    if state_data["mode"] == "start":
        event_type = RecurrentEvent.EventTypes.WORK_START
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=time.hour, minute=time.minute)
    elif state_data["mode"] == "end":
        event_type = RecurrentEvent.EventTypes.WORK_END
        end = now.replace(hour=23, minute=59, second=0, microsecond=0)
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
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"], RolesSchema.TEACHER)

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
        weekdays = EventService(db).available_work_weekdays(user.executor_id)
        await message.answer(
            replies.CHOOSE_WEEKDAY, reply_markup=Keyboards.weekdays(weekdays, WorkSchedule.create_weekend),
        )
    else:
        raise Exception("message", "Неизвестное событие", f"unknown weekend action {callback.data}")


@router.callback_query(F.data.startswith(WorkSchedule.create_weekend))
async def create_weekend(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"], RolesSchema.TEACHER)

    now = datetime.now()
    weekday = int(get_callback_arg(callback.data, WorkSchedule.create_weekend))
    start_of_week = now - timedelta(days=now.weekday())
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
