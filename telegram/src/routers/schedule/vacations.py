from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.routers.utils import student_permission
from src.service import VacationsService
from src.states import Vacations

router = Router()


@router.message(Command(Vacations.command))
@router.message(F.text == Vacations.text)
async def vacations_handler(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    await state.update_data(user_id=user.telegram_id)
    service = VacationsService(message, state)
    await service.vacations_list()


@router.callback_query(F.data.startswith(Vacations.add_vacation))
async def get_dates(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = VacationsService(message, state, callback)
    await service.get_dates()


@router.callback_query(Vacations.choose_dates)
async def add_vacation(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    service = VacationsService(message, state)
    await service.add_vacation()


@router.callback_query(F.data.startswith(Vacations.remove_vacation))
async def remove_vacation(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = VacationsService(message, state, callback)
    await service.remove_vacation()
