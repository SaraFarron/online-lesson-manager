from calendar import weekday
from datetime import datetime, timedelta

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core.config import TIME_FMT
from src.messages import replies
from src.schemas import EventCreate
from src.service.backend_client import BackendClient
from src.service.cache import Slot
from src.utils import send_message


class ScheduleService:
    def __init__(
        self, message: Message | CallbackQuery, state: FSMContext, callback: CallbackQuery | None = None,
    ) -> None:
        self.message = message
        self.callback = callback
        self.state = state
        self.backend_client = BackendClient()
        if not message.from_user:
            raise ValueError("Message must have a from_user attribute")
        if self.callback:
            self.telegram_id = self.callback.from_user.id
            self.username = self.callback.from_user.username
        else:
            self.telegram_id = message.from_user.id
            self.username = message.from_user.username

    def convert_free_slots(self, slots: list[Slot], step: int = 15, duration: int = 60) -> list[str]:
        res = []
        today, length = datetime.now().date(), timedelta(minutes=duration)
        for slot in slots:
            start_time = slot.start
            end_time = (datetime.combine(today, start_time) + length).time()
            while end_time <= slot.end:
                if end_time <= slot.end:
                    res.append(f"{start_time.strftime(TIME_FMT)}")
                start_time = (datetime.combine(today, start_time) + timedelta(minutes=step)).time()
                end_time = (datetime.combine(today, start_time) + length).time()
        return res

    def convert_weekdays(self, weekdays: dict[int, list[Slot]]) -> list[int]:
        return [wd for wd, slots in weekdays.items() if slots]

    async def _create_event(self, event: EventCreate):
        user_data = await self.backend_client.get_user_cache_data(self.telegram_id)
        if not user_data or not user_data.user_settings.token:
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return
        response = await self.backend_client.create_event(
            event,
            token=user_data.user_settings.token,
        )
        if not response:
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return
        await self.message.answer(replies.LESSON_ADDED)
        await self.state.clear()

        teacher_id = await self.backend_client.get_teacher_id(self.telegram_id)
        if not teacher_id:  # TODO notify admin about this error
            return
        lesson_time = event.start.strftime(TIME_FMT)
        if event.is_recurrent:
            weekday_name = weekday(event.day.weekday())
            message = f"{self.username} добавил(а) занятие на {weekday_name} в {lesson_time}"
        else:
            message = f"{self.username} добавил(а) занятие на {event.day} в {lesson_time}"
        await send_message(teacher_id, message)
