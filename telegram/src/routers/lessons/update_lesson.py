from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.messages import replies
from src.routers.utils import student_permission
from src.service import DeleteLessonService, MoveLessonService, UpdateLessonService
from src.states import UpdateLesson
from src.utils import get_callback_arg

router = Router()


@router.message(Command(UpdateLesson.command))
@router.message(F.text == UpdateLesson.text)
async def move_lesson_handler(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    await state.update_data(user_id=user.telegram_id)
    service = UpdateLessonService(message, state)
    await service.get_lesson()


@router.callback_query(F.data.startswith(UpdateLesson.choose_lesson))
async def choose_lesson(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = UpdateLessonService(message, state, callback)
    await service.choose_action()


@router.callback_query(F.data.startswith(UpdateLesson.move_or_delete))
async def move_or_delete(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    if not callback.data:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        await state.clear()
        return
    action = get_callback_arg(callback.data, UpdateLesson.move_or_delete)
    await state.update_data(action=action)

    match action:
        case "move":
            service = MoveLessonService(message, state, callback)
        case "delete":
            service = DeleteLessonService(message, state, callback)
        case _:
            await message.answer(replies.UNKNOWN_ACTION_ERR)
            await state.clear()
            return

    await service.perform_action()


# ---- MOVE ONE LESSON ---- #


@router.message(UpdateLesson.type_date)
async def type_date(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    service = MoveLessonService(message, state)
    await service.choose_time()


@router.callback_query(F.data.startswith(UpdateLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = MoveLessonService(message, state, callback)
    await service.move_lesson()


# ---- RECURRENT LESSON ---- #


@router.callback_query(F.data.startswith(UpdateLesson.once_or_forever))
async def once_or_forever(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return
    state_data = await state.get_data()
    action = state_data.get("action")
    if action is None:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        await state.clear()
        return

    match action:
        case "move":
            service = MoveLessonService(message, state, callback)
        case "delete":
            service = DeleteLessonService(message, state, callback)
        case _:
            await message.answer(replies.UNKNOWN_ACTION_ERR)
            await state.clear()
            return

    await service.once_or_forever()


# ---- RECURRENT LESSON MOVE FOREVER ---- #


@router.callback_query(F.data.startswith(UpdateLesson.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = MoveLessonService(message, state, callback)
    await service.choose_recurrent_time()


@router.callback_query(F.data.startswith(UpdateLesson.choose_recur_time))
async def choose_recur_time(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = MoveLessonService(message, state, callback)
    await service.move_recurrent_lesson()


# ---- RECURRENT LESSON ACTION ONCE ---- #


@router.message(UpdateLesson.type_recur_date)
async def type_recur_date(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return
    state_data = await state.get_data()
    action = state_data.get("action")
    if action is None:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        await state.clear()
        return

    match action:
        case "move":
            service = MoveLessonService(message, state)
            await service.type_recur_date()
        case "delete":
            service = DeleteLessonService(message, state)
            await service.cancel_once()
        case _:
            await message.answer(replies.UNKNOWN_ACTION_ERR)
            await state.clear()
            return


# ---- RECURRENT LESSON MOVE ONCE ---- #


@router.message(UpdateLesson.type_new_date)
async def type_recur_new_date(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    service = MoveLessonService(message, state)
    await service.type_new_recur_date()


@router.callback_query(F.data.startswith(UpdateLesson.choose_recur_new_time))
async def choose_recur_new_time(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = MoveLessonService(message, state)
    await service.move_recur_once()
