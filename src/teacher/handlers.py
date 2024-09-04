import json
from datetime import datetime

import aiofiles
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import help, messages
from config.config import ADMINS, WORK_SCHEDULE_TIMETABLE_PATH
from logger import log_func
from teacher.callbacks import EditWeekdayCallBack, WorkWeekdayCallBack
from teacher.keyboards import working_hours_keyboard, working_hours_on_day_keyboard
from teacher.states import NewTime

router: Router = Router()


@router.message(Command("edit_work_schedule"))
@router.message(F.text == help.EDIT_WORKING_HOURS)
@log_func
async def edit_work_schedule(message: Message) -> None:
    """Handler receives messages with `/edit_work_schedule` command."""
    if message.from_user.id not in ADMINS:
        await message.answer(messages.PERMISSION_DENIED)
    await message.answer(messages.SHOW_WORK_SCHEDULE, reply_markup=working_hours_keyboard())


@router.callback_query(WorkWeekdayCallBack.filter())
@log_func
async def choose_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_weekday` state."""
    await state.update_data(weekday=callback.data.replace("choose_work_weekday:", ""))
    weekday_k6d = working_hours_on_day_keyboard(callback.data.replace("choose_work_weekday:", ""))
    await callback.message.answer(messages.EDIT_WEEKDAY, reply_markup=weekday_k6d)


@router.callback_query(EditWeekdayCallBack.filter())
@log_func
async def edit_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `edit_weekday` state."""
    period = callback.data.replace("edit_weekday:", "")
    state_data = await state.get_data()
    weekday = state_data["weekday"]
    match period:
        case "daystart":
            await state.update_data(period="daystart")
            await callback.message.answer(messages.SEND_NEW_TIME)
        case "dayend":
            await state.update_data(period="dayend")
            await callback.message.answer(messages.SEND_NEW_TIME)
        case "addbreak":
            await state.update_data(period="addbreak")
            await callback.message.answer(messages.SEND_BREAK_TIME)
        case "rmbreak":
            await state.update_data(period="rmbreak")
            async with aiofiles.open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
                data = json.loads(await f.read())
            data[weekday].pop("break")
            async with aiofiles.open(WORK_SCHEDULE_TIMETABLE_PATH, "w") as f:
                await f.write(json.dumps(data))
            await callback.message.answer(messages.BREAK_REMOVED)
        case "breakstart":
            await state.update_data(period="breakstart")
            await callback.message.answer(messages.SEND_NEW_TIME)
        case "breakend":
            await state.update_data(period="breakend")
            await callback.message.answer(messages.SEND_NEW_TIME)
    await state.set_state(NewTime.new_time)


@router.message(NewTime.new_time)
@log_func
async def new_time(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `new_time` state."""
    state_data = await state.get_data()
    period = state_data["period"]
    try:
        if period == "addbreak":
            start_end = message.text.split("-")
            datetime.strptime(start_end[0], "%H:%M")  # noqa: DTZ007
            datetime.strptime(start_end[1], "%H:%M")  # noqa: DTZ007
        else:
            datetime.strptime(message.text, "%H:%M")  # noqa: DTZ007
    except ValueError:
        await message.answer(messages.INVALID_TIME)
        return
    async with aiofiles.open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
        data = json.loads(await f.read())
    match period:
        case "daystart":
            data[state_data["weekday"]]["start"] = message.text
        case "dayend":
            data[state_data["weekday"]]["end"] = message.text
        case "breakstart":
            data[state_data["weekday"]]["break"]["start"] = message.text
        case "breakend":
            data[state_data["weekday"]]["break"]["end"] = message.text
        case "addbreak":
            start_end = message.text.split("-")
            data[state_data["weekday"]]["break"] = {"start": start_end[0], "end": start_end[1]}
    async with aiofiles.open(WORK_SCHEDULE_TIMETABLE_PATH, "w") as f:
        await f.write(json.dumps(data))
    await message.answer(messages.TIME_UPDATED)
    await state.clear()
