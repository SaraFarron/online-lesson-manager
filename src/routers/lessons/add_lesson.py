from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.callbacks import AddLessonCallback
from src.help import Commands
from src.keyboards import Keyboards
from src.middlewares import DatabaseMiddleware
from src.models import Event
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import get_callback_arg, parse_date, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class AddLesson(StatesGroup):
    scene = "add_lesson"
    command = "/" + scene
    choose_date = State()


@router.message(Command(AddLesson.command))
@router.message(F.text == Commands.ADD_ONE_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)
    await state.update_data(user_id=user.telegram_id)
    await message.answer("Введите дату занятия, формат ГГГГ ММ ДД")
    await state.set_state(AddLesson.choose_date)
    EventHistoryRepo(db).create(user.username, AddLesson.scene, "handler", "")


@router.message(AddLesson.choose_date)
async def choose_date(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)
    date = parse_date(message.text)
    if date is None:
        await state.set_state(AddLesson.choose_date)
        await message.answer("Неверный формат даты, допустимые: ГГГГ ММ ДД, ГГГГ.ММ.ДД, ГГГГ-ММ-ДД")
        EventHistoryRepo(db).create(
            user.username, AddLesson.scene, "choose_date", f"wrong date fmt `{message.text}`"
        )
        return
    await state.update_data(day=date)

    available_time = EventRepo(db).available_time(user.executor_id, date)
    available_time = [s for s, e in available_time]
    await message.answer("Выберите время", reply_markup=Keyboards.choose_time(available_time, AddLessonCallback.choose_time))
    EventHistoryRepo(db).create(user.username, AddLesson.scene, "choose_date", str(date))


@router.callback_query(F.data.startswith(AddLessonCallback.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)
    date = state_data["day"]
    time = datetime.strptime(
        get_callback_arg(callback.data, AddLessonCallback.choose_time),
        "%H:%M"
    ).time()

    lesson = Event(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=Event.EventTypes.LESSON,
        start=datetime.combine(date, time),
        end=datetime.combine(date, time.replace(hour=time.hour + 1)),
    )
    db.add(lesson)
    db.commit()
    await message.answer("Занятие добавлено")
    EventHistoryRepo(db).create(user.username, AddLesson.scene, "choose_time", str(time))
    await state.clear()
