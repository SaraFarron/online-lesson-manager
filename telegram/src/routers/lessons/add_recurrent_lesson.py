from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.routers.utils import student_permission
from src.service import AddRecurrentLessonService
from src.states import AddRecurrentLesson

router = Router()


@router.message(Command(AddRecurrentLesson.command))
@router.message(F.text == AddRecurrentLesson.text)
async def add_lesson_handler(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    await state.update_data(user_id=user.telegram_id)
    service = AddRecurrentLessonService(message, state)
    await service.get_weekday()


@router.callback_query(F.data.startswith(AddRecurrentLesson.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = AddRecurrentLessonService(message, state, callback)
    await service.available_time()


@router.callback_query(F.data.startswith(AddRecurrentLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = AddRecurrentLessonService(message, state, callback)
    await service.create()
