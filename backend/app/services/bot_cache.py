from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentEvent, User
from app.repositories import UserRepository
from app.schemas import TelegramCacheResponse
from app.services import AuthService
from app.services.events import EventService


class BotCacheService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.event_service = EventService(session)
        self.auth_service = AuthService(session)

    def _convert_schedule_to_cache_response(
        self, schedule: dict[str, list[Event | RecurrentEvent]]
    ) -> dict[str, list[dict[str, str]]]:
        cache_schedule: dict[str, list[dict[str, str]]] = {}
        for date, events in schedule.items():
            cache_schedule[date] = []
            for event in events:
                cache_schedule[date].append(
                    {
                        "id": event.id,
                        "type": event.title,
                        "start": event.start.time().isoformat(),
                        "is_recurrent": isinstance(event, RecurrentEvent),
                    }
                )
        return cache_schedule

    async def get_student_schedule(self, user: User) -> TelegramCacheResponse:
        start = datetime.now()
        end = start + timedelta(days=14)
        start, end = start.date(), end.date()
        schedule = await self.event_service.get_schedule_range(user, start, end)
        free_slots = await self.event_service.get_free_slots_range(user, start, end)
        recurrent_free_slots = {}
        for weekday in range(7):
            recurrent_free_slots[str(weekday)] = await self.event_service.get_recurrent_free_slots(user, weekday)
        new_token = await self.auth_service.generate_token(user.id)
        return TelegramCacheResponse(
            free_slots=free_slots,
            recurrent_free_slots=recurrent_free_slots,
            schedule=self._convert_schedule_to_cache_response(schedule),
            user_settings={
                "teacher_telegram_id": user.teacher.telegram_id if user.teacher else None,
                "token": new_token.token,
            },  # Placeholder for user settings
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
