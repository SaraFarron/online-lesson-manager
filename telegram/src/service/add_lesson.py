from datetime import date, datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core.config import DATE_FMT
from src.keyboards import choose_time
from src.messages import replies
from src.schemas import EventCreate
from src.service.base import ScheduleService
from src.states import AddLesson
from src.utils import get_callback_arg, parse_date, send_message


class AddLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)

    async def get_day(self) -> dict | None:
        if not self.message.text:
            await self.state.set_state(AddLesson.choose_date)
            await self.message.answer(replies.WRONG_DATE_FMT)
            return None
        day = parse_date(self.message.text)
        today = datetime.now().date()
        if day is None:
            await self.state.set_state(AddLesson.choose_date)
            await self.message.answer(replies.WRONG_DATE_FMT)
            return None
        if today > day:
            await self.state.set_state(AddLesson.choose_date)
            await self.message.answer(replies.CHOOSE_FUTURE_DATE)
            if len(self.message.text) <= 7:
                await self.message.answer(replies.ADD_YEAR)
            return None
        return {"day": day}

    async def available_time(self, day: date):
        free_slots = await self.backend_client.get_user_free_slots(self.telegram_id)
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
            reply_markup=choose_time(available_time, AddLesson.choose_time),
        )

    async def create(self):
        if not self.callback or not self.callback.data:
            await self.state.set_state(AddLesson.choose_time)
            await self.message.answer(replies.WRONG_TIME_FMT)
            return
        state_data = await self.state.get_data()
        date = (
            datetime.strptime(state_data["day"], DATE_FMT) if isinstance(state_data["day"], str) else state_data["day"]
        )
        time = get_callback_arg(self.callback.data, AddLesson.choose_time)

        user_data = await self.backend_client.get_user_cache_data(self.telegram_id)
        if not user_data or not user_data.user_settings.token:
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return
        response = await self.backend_client.create_event(
            EventCreate(
                title="Урок",
                day=date,
                start=datetime.strptime(time, "%H:%M").time(),
                duration=60,  # TODO make it configurable
                is_recurrent=False,
            ),
            token=user_data.user_settings.token,
        )
        if not response:
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return

        await self.message.answer(replies.LESSON_ADDED)
        teacher_id = await self.backend_client.get_teacher_id(self.telegram_id)
        if not teacher_id:  # TODO notify admin about this error
            return
        message = f"{self.username} добавил(а) занятие на {date} в {time}"
        await send_message(teacher_id, message)
        await self.state.clear()
