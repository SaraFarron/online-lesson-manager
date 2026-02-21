from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.messages import replies
from src.schemas import EventCreate
from src.service.base import ScheduleService
from src.states import AddRecurrentLesson
from src.utils import get_callback_arg, get_next_weekday, parse_time


class AddRecurrentLessonService(ScheduleService):
    def __init__(self, message: Message, state: FSMContext, callback: CallbackQuery | None = None) -> None:
        super().__init__(message, state, callback)

    async def get_weekday(self):
        await self.choose_weekday_action(AddRecurrentLesson.choose_weekday)

    async def available_time(self):
        weekday = int(get_callback_arg(self.callback.data, AddRecurrentLesson.choose_weekday))
        await self.choose_time_action(weekday, AddRecurrentLesson.choose_time)

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
                title="Урок",
                day=day.date(),
                start=time,
                duration=60,  # TODO make it configurable
                is_recurrent=True,
            ),
        )
