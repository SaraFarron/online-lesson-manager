from src.service.backend_client import BackendClientError
from datetime import date, datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core.config import DATE_FMT
from src.keyboards import choose_lesson, choose_move_or_delete, once_or_forever, choose_time
from src.messages import replies
from src.schemas import EventCreate
from src.service.base import ScheduleService
from src.states import UpdateLesson
from src.utils import get_callback_arg, parse_date, parse_time


class UpdateLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)

    async def get_lesson(self):
        try:
            lessons = await self.backend_client.get_user_schedule(self.telegram_id)
        except BackendClientError as e:
            await self.message.answer(e.detail)
            await self.state.clear()
            return

        if not lessons:
            await self.message.answer(replies.NO_LESSONS)
            await self.state.clear()
            return
        buttons = self.convert_lessons_to_buttons(lessons)
        if buttons:
            await self.message.answer(
                replies.CHOOSE_LESSON, reply_markup=choose_lesson(buttons, UpdateLesson.choose_lesson),
            )
        else:
            await self.message.answer(replies.NO_LESSONS)
            await self.state.clear()
            return
    
    async def choose_action(self):
        lesson_id = get_callback_arg(self.callback.data, UpdateLesson.choose_lesson)
        if not lesson_id or not lesson_id.isnumeric():
            await self.state.set_state(UpdateLesson.choose_lesson)
            await self.message.answer(replies.CHOOSE_LESSON)
            return
        await self.state.update_data(lesson_id=int(lesson_id))
        await self.message.answer(
            replies.MOVE_OR_DELETE, reply_markup=choose_move_or_delete(UpdateLesson.move_or_delete),
        )


class MoveLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)
    
    async def perform_action(self):
        state_data = await self.state.get_data()
        # Move one event
        if state_data["lesson_id"] % 2:
            await self.state.set_state(UpdateLesson.type_date)
            await self.message.answer(replies.CHOOSE_LESSON_DATE)

        # Move recurrent event
        else:
            await self.message.answer(
                replies.MOVE_ONCE_OR_FOREVER,
                reply_markup=once_or_forever(UpdateLesson.once_or_forever),
            )
    
    async def choose_time(self):
        day = parse_date(self.message.text)
        today = datetime.now().date()
        if day is None:
            await self.message.answer(replies.WRONG_DATE_FMT)
            await self.state.set_state(UpdateLesson.type_date)
            return
        if today > day:
            await self.message.answer(replies.CHOOSE_FUTURE_DATE)
            if len(self.message.text) <= 5:
                await self.message.answer(replies.ADD_YEAR)
            await self.state.set_state(UpdateLesson.type_date)
            return

        await self.state.update_data(day=day)
        
        try:
            free_slots = await self.backend_client.get_user_free_slots(self.telegram_id)
        except BackendClientError as e:
            await self.message.answer(e.detail)
            await self.state.clear()
            return

        if not free_slots or str(day) not in free_slots:
            await self.message.answer(replies.NO_TIME)
            await self.state.clear()
            return

        available_time = self.convert_free_slots(free_slots[str(day)])
        if not available_time:
            await self.message.answer(replies.NO_TIME)
            await self.state.clear()
            return
        
        await self.message.answer(
            replies.CHOOSE_TIME,
            reply_markup=choose_time(available_time, UpdateLesson.choose_time),
        )
    
    async def move_lesson(self):
        state_data = await self.state.get_data()
        event_id, day = state_data["lesson_id"], state_data["day"]
        time = parse_time(get_callback_arg(self.callback.data, UpdateLesson.choose_time))
        if time is None:
            await self.message.answer(replies.WRONG_TIME_FMT)
            await self.state.set_state(UpdateLesson.choose_time)
            return

        await self._update_event(event_id, EventCreate(title="Перенос", day=day, start=time))


class DeleteLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)

    async def perform_action(self):
        state_data = await self.state.get_data()
        # Delete one event
        if state_data["lesson_id"] % 2:
            await self._delete_event(state_data["lesson_id"])
            return

        # Delete recurrent event
        await self.message.answer(
            replies.DELETE_ONCE_OR_FOREVER,
            reply_markup=once_or_forever(UpdateLesson.once_or_forever),
        )

