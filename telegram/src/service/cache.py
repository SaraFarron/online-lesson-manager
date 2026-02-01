from datetime import date, datetime, time, timedelta

from cachetools import TTLCache
from pydantic import BaseModel


class Event(BaseModel):
    type: str
    start: datetime


class Slot(BaseModel):
    start: time
    end: time


class UserSettings(BaseModel):
    telegram_id: int
    morning_notification: time | None
    role: str
    teacher_id: int
    code: str | None = None


class UserCacheData(BaseModel):
    free_slots: dict[str, list[Slot]]
    recurrent_free_slots: dict[int, list[Slot]]
    schedule: dict[str, list[Event]]
    user_settings: UserSettings


class BotCache:
    def __init__(self):
        # Разные кэши для разных типов данных
        self.schedule_cache = TTLCache(maxsize=100, ttl=300)  # 5 минут
        self.teachers_cache = TTLCache(maxsize=100, ttl=3600)  # 1 час

    def get_cache(self):
        return self.schedule_cache
    
    def set_cache(self, cache_data: dict):
        self.schedule_cache.update(cache_data)

    def invalidate_schedule(self, teacher_id: int):
        """Вызываем когда расписание изменилось"""
        self.schedule_cache.pop(teacher_id, None)

    def get_teacher(self, code: str):
        return self.teachers_cache.get(code)

# Глобальный экземпляр
cache = BotCache()
