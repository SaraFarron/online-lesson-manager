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
        await self.state.update_data(lesson=int(lesson_id))
        await self.message.answer(
            replies.MOVE_OR_DELETE, reply_markup=choose_move_or_delete(UpdateLesson.move_or_delete),
        )


class MoveLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)
    
    async def perform_action(self):
        # Move one event
        if action == "move" and state_data["lesson"].startswith("e"):
            await state.set_state(MoveLesson.type_date)
            await message.answer(replies.CHOOSE_LESSON_DATE)

        # Move recurrent event
        elif action == "move" and state_data["lesson"].startswith("re"):
            await state.update_data(action=action)
            await message.answer(
                replies.MOVE_ONCE_OR_FOREVER,
                reply_markup=Keyboards.once_or_forever(MoveLesson.once_or_forever),
            )


class DeleteLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)

    async def perform_action(self):
        # Delete one event
        if action == "delete" and state_data["lesson"].startswith("e"):
            lesson = EventService(db).cancel_event(int(state_data["lesson"].replace("e", "")))
            EventHistoryRepo(db).create(user.get_username(), MoveLesson.scene, "deleted_one_lesson", str(lesson))
            await message.answer(replies.LESSON_DELETED)
            executor_tg = UserRepo(db).executor_telegram_id(user)
            await send_message(executor_tg, f"{user.get_username()} отменил(а) {lesson}")
            await state.clear()
            return

        # Delete recurrent event
        if action == "delete" and state_data["lesson"].startswith("re"):
            await state.update_data(action=action)
            await message.answer(
                replies.DELETE_ONCE_OR_FOREVER,
                reply_markup=Keyboards.once_or_forever(MoveLesson.once_or_forever),
            )

