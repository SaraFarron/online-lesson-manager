from datetime import datetime, timedelta

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core.config import TIME_FMT
from src.service.backend_client import BackendClient
from src.service.cache import Slot


class ScheduleService:
    def __init__(self, message: Message | CallbackQuery, state: FSMContext, callback: CallbackQuery | None = None) -> None:
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
