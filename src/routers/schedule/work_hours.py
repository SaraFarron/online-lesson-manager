from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.keyboards import Keyboards, AdminCommands
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import RecurrentEvent, User
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import parse_time, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class WorkHours(StatesGroup):
    scene = "manage_work_hours"
    command = "/" + scene
    base_callback = scene + "/"
    action = f"{base_callback}action/"
    choose_weekday = f"{base_callback}choose_weekday/"
    choose_time = State()


@router.message(Command(WorkHours.command))
@router.message(F.text == AdminCommands.MANAGE_WORK_HOURS.value)
async def manage_work_hours_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    await state.update_data(user_id=user.telegram_id)
    work_hours = EventRepo(db).work_hours(user)
    await message.answer(replies.CHOOSE_WH_ACTION, reply_markup=Keyboards.work_hours(work_hours, WorkHours.action))


@router.callback_query(F.data.startswith(WorkHours.action))
async def action(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    await state.update_data(user_id=user.telegram_id)

    action_type = callback.data.split("/")[-1]
    if action_type.startswith("delete"):
        if action_type.endswith("start"):
            time = EventRepo(db).delete_work_hour_setting(user.executor_id, "start")
            EventHistoryRepo(db).create(user.username, WorkHours.scene, "deleted_start", str(time))
        elif action_type.endswith("end"):
            time = EventRepo(db).delete_work_hour_setting(user.executor_id, "end")
            EventHistoryRepo(db).create(user.username, WorkHours.scene, "deleted_end", str(time))
        await message.answer(replies.WORK_HOURS_DELETED)
    elif action_type.startswith("add"):
        if action_type.endswith("start"):
            await state.update_data(mode="start")
        elif action_type.endswith("end"):
            await state.update_data(mode="end")
        await state.set_state(WorkHours.choose_time)
        await message.answer(replies.CHOOSE_TIME)
    else:
        raise Exception("message", "Неизвестное действие", f"callback.data is unknown: {callback.data}")


@router.message(WorkHours.choose_time)
async def choose_time(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)
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
    EventHistoryRepo(db).create(user.username, WorkHours.scene, f"added_{state_data['mode']}", str(event))
    await state.clear()
