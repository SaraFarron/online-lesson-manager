from datetime import datetime, timedelta

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core.config import CHANGE_DELTA, SHORT_DATE_FMT, TIME_FMT, WEEKDAY_MAP
from src.messages import replies
from src.schemas import EventCreate
from src.service.backend_client import BackendClient, BackendClientError
from src.service.cache import Event, Slot
from src.utils import send_message


class ScheduleService:
    def __init__(
        self,
        message: Message | CallbackQuery,
        state: FSMContext,
        callback: CallbackQuery | None = None,
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

    def convert_lessons_to_buttons(self, data: dict[str, list[Event]]) -> dict[int, str]:
        buttons = {}
        today, now = datetime.now().date(), (datetime.now() + CHANGE_DELTA).time()
        for day, lessons in data.items():
            current_day = datetime.strptime(day, "%Y-%m-%d").date()
            if current_day < today:
                continue
            for lesson in lessons:
                if current_day == today and lesson.start < now:
                    continue
                weekday = WEEKDAY_MAP[current_day.weekday()]["short"]
                lesson_time = lesson.start.strftime(TIME_FMT)
                if lesson.is_recurrent:
                    button_text = f"{lesson.type} {weekday} в {lesson_time}"
                else:
                    button_text = f"{lesson.type} {current_day.strftime(SHORT_DATE_FMT)} в {lesson_time}"
                buttons[lesson.id] = button_text
        return buttons

    async def _create_event(self, event: EventCreate):
        try:
            user_data = await self.backend_client.get_user_cache_data(self.telegram_id)
        except BackendClientError as e:
            await self.message.answer(e.detail)
            await self.state.clear()
            return

        if not user_data or not user_data.user_settings.token:
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return
        try:
            await self.backend_client.create_event(
                event,
                token=user_data.user_settings.token,
            )
        except BackendClientError as e:
            await self.message.answer(e.detail)
            await self.state.clear()
            return

        await self.message.answer(replies.LESSON_ADDED)
        await self.state.clear()

        teacher_id = await self.backend_client.get_teacher_id(self.telegram_id)
        if not teacher_id:  # TODO notify admin about this error
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return

        lesson_time = event.start.strftime(TIME_FMT)
        if event.is_recurrent:
            weekday_name = WEEKDAY_MAP[event.day.weekday()]["long"]
            message = f"{self.username} добавил(а) занятие на {weekday_name} в {lesson_time}"
        else:
            day = event.day.strftime(SHORT_DATE_FMT)
            message = f"{self.username} добавил(а) занятие на {day} в {lesson_time}"
        await send_message(teacher_id, message)

    async def _update_event(self, event_id: int, event: EventCreate):
        try:
            user_data = await self.backend_client.get_user_cache_data(self.telegram_id)
        except BackendClientError as e:
            await self.message.answer(e.detail)
            await self.state.clear()
            return

        if not user_data or not user_data.user_settings.token:
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return

        try:
            await self.backend_client.update_event(
                event_id,
                event,
                token=user_data.user_settings.token,
            )
        except BackendClientError as e:
            if e.status == 404:
                await self.message.answer(replies.LESSON_NOT_FOUND_ERR)
                await self.state.clear()
                return
            await self.message.answer(e.detail)
            await self.state.clear()
            return

        await self.message.answer(replies.LESSON_MOVED)
        await self.state.clear()
        return

    async def _delete_event(self, event_id: int):
        user_data = await self.backend_client.get_user_cache_data(self.telegram_id)
        if not user_data or not user_data.user_settings.token:
            await self.message.answer(replies.SOMETHING_WENT_WRONG)
            await self.state.clear()
            return

        try:
            await self.backend_client.delete_event(event_id, user_data.user_settings.token)
        except BackendClientError as e:
            if e.status == 404:
                await self.message.answer(replies.LESSON_NOT_FOUND_ERR)
                await self.state.clear()
                return
            await self.message.answer(e.detail)
            await self.state.clear()
            return

        await self.message.answer(replies.LESSON_DELETED)
        await self.state.clear()
        return
