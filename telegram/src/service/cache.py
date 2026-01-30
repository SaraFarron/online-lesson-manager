from datetime import date, datetime, time, timedelta

from cachetools import TTLCache
from pydantic import BaseModel


class Event(BaseModel):
    type: str
    start: datetime


class Slot(BaseModel):
    start: time
    end: time


class UserCacheData(BaseModel):
    free_slots: dict[str, list[Slot]]
    recurrent_free_slots: dict[int, list[Slot]]
    schedule: dict[str, list[Event]]
    user_settings: dict


class BotCache:
    def __init__(self):
        # Разные кэши для разных типов данных
        self.schedule_cache = TTLCache(maxsize=100, ttl=300)  # 5 минут
        self.student_lessons_cache = TTLCache(maxsize=500, ttl=120)  # 2 минуты
        self.profile_cache = TTLCache(maxsize=500, ttl=600)  # 10 минут

    def get_user_schedule(self, user_id: int) -> UserCacheData | None:
        cached = self.schedule_cache.get(user_id)
        if cached:
            return UserCacheData(**cached)
        return None

    def set_user_schedule(self, user_id: int, schedule: UserCacheData):
        self.schedule_cache[user_id] = schedule.model_dump()

    def get_user_schedule_day(self, user_id: int, day: date):
        cached = self.get_user_schedule(user_id)
        if cached:
            return cached.schedule.get(str(day), [])
        return None
    
    def get_user_schedule_week(self, user_id: int, start: date):
        cached = self.get_user_schedule(user_id)
        if cached is None:
            return None
        schedule = {}
        end = start + timedelta(days=7)
        while start <= end:
            schedule[start] = self.get_user_schedule_day(start)
            start += timedelta(days=1)
        return schedule

    def invalidate_student_lessons(self, student_id: int):
        """Вызываем после бронирования/отмены урока"""
        self.student_lessons_cache.pop(student_id, None)

    def invalidate_schedule(self, teacher_id: int):
        """Вызываем когда расписание изменилось"""
        self.schedule_cache.pop(teacher_id, None)


# Глобальный экземпляр
cache = BotCache()
