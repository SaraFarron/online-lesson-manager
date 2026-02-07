from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories import UserRepository
from app.schemas import TelegramCacheResponse
from app.services.events import EventService


class BotCacheService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.event_service = EventService(session)

    async def get_student_schedule(self, user: User) -> TelegramCacheResponse:
        start = datetime.now()
        end = start + timedelta(days=14)
        schedule = await self.event_service.get_schedule_range(user, start, end)
        free_slots = await self.event_service.get_free_slots_range(user, start, end)
        recurrent_free_slots = {}
        for weekday in range(7):
            recurrent_free_slots[str(weekday)] = await self.event_service.get_recurrent_free_slots(user, weekday)
        return TelegramCacheResponse(
            free_slots=free_slots,
            recurrent_free_slots=recurrent_free_slots,
            schedule=schedule,
            user_settings={},  # Placeholder for user settings
        )

    async def get_teacher_schedule(self, user: User) -> dict[int, TelegramCacheResponse]:
        schedules: dict[int, TelegramCacheResponse] = {}
        for student in user.students:
            schedules[student.id] = await self.get_student_schedule(student)
        schedules[user.id] = await self.get_student_schedule(user)
        return schedules

    async def get_user_schedule(self, telegram_id: int) -> dict[int, TelegramCacheResponse]:
        user = await UserRepository(self.session).get_by_telegram_id(telegram_id)
        if not user:
            return {}
        if user.role == User.Roles.STUDENT:
            return {telegram_id: await self.get_student_schedule(user)}
        elif user.role == User.Roles.TEACHER:
            return await self.get_teacher_schedule(user)
        else:
            return {}
