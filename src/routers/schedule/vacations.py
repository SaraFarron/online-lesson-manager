from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core.config import WEEKDAY_MAP
from src.keyboards import Commands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import RecurrentEvent, User, Event
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import parse_time, telegram_checks, get_callback_arg

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class Vacations(StatesGroup):
    scene = "manage_work_schedule"
    command = "/" + scene
    base_callback = scene + "/"
    edit_vacations = f"{base_callback}edit_vacations"
    add_vacation = f"{base_callback}add_vacation/"
    remove_vacation = f"{base_callback}remove_vacation/"


@router.message(Command(Vacations.command))
@router.message(F.text == Commands.VACATIONS.value)
async def manage_work_schedule_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)
    await state.update_data(user_id=user.telegram_id)

    vacations = EventRepo(db).vacations(user.id)
    await message.answer(replies.CHOOSE_ACTION, Keyboards.vacations(vacations, Vacations.edit_vacations))


@router.callback_query(F.data.startswith(Vacations.edit_vacations))
async def edit_vacations(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    action = get_callback_arg(callback.data, Vacations.edit_vacations)
    if action.startswith("delete_vacation"):
        event_id = int(action.split("/")[-1])
        event = db.get(Event, event_id)
        # TODO
