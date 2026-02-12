from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.keyboards import choose_time, choose_weekday
from src.messages import replies
from src.schemas import EventCreate
from src.service.base import ScheduleService
from src.states import AddRecurrentLesson
from src.utils import get_callback_arg, get_next_weekday, parse_time


class AddRecurrentLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)

    async def get_weekday(self):
        free_slots = await self.backend_client.get_user_recurrent_free_slots(self.telegram_id)
        if not free_slots:
            await self.message.answer(replies.NO_TIME)
            await self.state.clear()
            return
        weekdays = self.convert_weekdays(free_slots)
        await self.message.answer(
            replies.CHOOSE_WEEKDAY,
            reply_markup=choose_weekday(weekdays, AddRecurrentLesson.choose_weekday),
        )

    async def available_time(self):
        weekday = int(get_callback_arg(self.callback.data, AddRecurrentLesson.choose_weekday))
        free_slots = await self.backend_client.get_user_recurrent_free_slots(self.telegram_id)
        if not free_slots or weekday not in free_slots:
            await self.message.answer(replies.NO_TIME)
            await self.state.clear()
            return
        await self.state.update_data(weekday=weekday)
        available_time = self.convert_free_slots(free_slots[weekday])
        await self.message.answer(
            replies.CHOOSE_TIME,
            reply_markup=choose_time(available_time, AddRecurrentLesson.choose_time),
        )

    async def create(self):
        time = parse_time(get_callback_arg(self.callback.data, AddRecurrentLesson.choose_time))
        if time is None:
            await self.state.set_state(AddRecurrentLesson.choose_time)
            await self.message.answer(replies.WRONG_TIME_FMT)
            return
        state_data = await self.state.get_data()
        weekday = state_data.get("weekday")
        if weekday is None:
            await self.state.set_state(AddRecurrentLesson.choose_weekday)
            await self.message.answer(replies.CHOOSE_WEEKDAY)
            return
        day = get_next_weekday(weekday)
        await self._create_event(
            EventCreate(
                title="Еженедельный урок",
                day=day.date(),
                start=time,
                duration=60,  # TODO make it configurable
                is_recurrent=True,
            ),
        )
