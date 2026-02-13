from datetime import date, datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core.config import DATE_FMT
from src.keyboards import choose_lesson, choose_move_or_delete
from src.messages import replies
from src.schemas import EventCreate
from src.service.base import ScheduleService
from src.states import UpdateLesson
from src.utils import get_callback_arg, parse_date, parse_time


class UpdateLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)

    async def get_lesson(self):
        lessons = await self.backend_client.get_user_schedule(self.telegram_id)
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
                reply_markup=Keyboards.once_or_forever(UpdateLesson.once_or_forever),
            )


class DeleteLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)

    async def perform_action(self):
        state_data = await self.state.get_data()
        # Delete one event
        if state_data["lesson_id"] % 2:
            await self._delete_event(state_data["lesson_id"])

        # Delete recurrent event
        await self.message.answer(
            replies.DELETE_ONCE_OR_FOREVER,
            reply_markup=Keyboards.once_or_forever(UpdateLesson.once_or_forever),
        )

