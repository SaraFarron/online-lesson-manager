from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.db.models import Event
from src.db.repositories import EventHistoryRepo
from src.keyboards import Commands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.services import EventRepo, UserService
from src.utils import get_callback_arg, parse_date, send_message, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class Vacations(StatesGroup):
    scene = "vacations"
    command = "/" + scene
    base_callback = scene + "/"
    edit_vacations = f"{base_callback}edit_vacations"
    choose_dates = State()


@router.message(Command(Vacations.command))
@router.message(F.text == Commands.VACATIONS.value)
async def vacations_handler(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user(message)
    await state.update_data(user_id=user.telegram_id)

    vacations = EventRepo(db).vacations(user.id)
    await message.answer(replies.CHOOSE_ACTION, reply_markup=Keyboards.vacations(vacations, Vacations.edit_vacations))


@router.callback_query(F.data.startswith(Vacations.edit_vacations))
async def edit_vacations(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)

    action = get_callback_arg(callback.data, Vacations.edit_vacations)
    if action.startswith("delete_vacation"):
        event_id = int(action.split("/")[-1])
        event = db.get(Event, event_id)
        event_str = f"{event.start.date()} - {event.end.date()}"
        db.delete(event)
        db.commit()
        await message.answer(replies.VACATION_DELETED)
        username = user.username if user.username else user.full_name
        EventHistoryRepo(db).create(username, Vacations.scene, "delete_vacation", event_str)
        executor_tg = UserService(db).executor_telegram_id(user)
        await send_message(executor_tg, f"{username} удалил(а) Каникулы {event_str}")
        await state.clear()
    elif action.startswith("add_vacation"):
        await message.answer(replies.CHOOSE_DATES)
        await state.set_state(Vacations.choose_dates)
    else:
        raise Exception("message", "Неизвестное событие", f"unknown action: {callback.data}")


@router.message(Vacations.choose_dates)
async def choose_time(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)

    try:
        dates = [d.strip() for d in message.text.split("-")]
        start, end = parse_date(dates[0]), parse_date(dates[1])
        assert start is not None
        assert end is not None
    except (ValueError, IndexError, AssertionError):
        await message.answer(replies.WRONG_DATES_FMT)
        await state.set_state(Vacations.choose_dates)
        return

    if start > end:
        await message.answer(replies.START_LT_END)
        await state.set_state(Vacations.choose_dates)
        return

    event = Event(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=Event.EventTypes.VACATION,
        start=datetime.combine(start, datetime.now().time().replace(hour=0, minute=0)),
        end=datetime.combine(end, datetime.now().time().replace(hour=23, minute=59)),
    )
    db.add(event)
    db.commit()
    await message.answer(replies.VACATION_ADDED)
    event_str = f"{event.start.date()} - {event.end.date()}"
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(username, Vacations.scene, "added_vacation", event_str)
    executor_tg = UserService(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{username} добавил(а) {event}")
    await state.clear()
