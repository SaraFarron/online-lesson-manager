from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.messages import replies
from src.routers.utils import student_permission
from src.service import AddLessonService
from src.states import AddLesson

router = Router()


@router.message(Command(AddLesson.command))
@router.message(F.text == AddLesson.text)
async def add_lesson_handler(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    await state.update_data(user_id=user.telegram_id)
    await message.answer(replies.CHOOSE_LESSON_DATE)
    await state.set_state(AddLesson.choose_date)


@router.message(AddLesson.choose_date)
async def choose_date(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    service = AddLessonService(message, state)
    new_state = await service.get_day()

    if not new_state:
        return

    await state.update_data(new_state)
    await service.available_time(new_state["day"])


@router.callback_query(F.data.startswith(AddLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = AddLessonService(message, state)
    await service.create()
