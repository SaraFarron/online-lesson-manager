from datetime import datetime, time

from cachetools import TTLCache
from pydantic import BaseModel


class Event(BaseModel):
    type: str
    start: datetime


class Slot(BaseModel):
    start: time
    end: time


class UserSettings(BaseModel):
    telegram_id: int = 0
    teacher_telegram_id: int | None = None
    full_name: str = ""
    username: str | None = None
    role: str = "student"
    token: str | None = None


class UserCacheData(BaseModel):
    free_slots: dict[str, list[Slot]]  # date -> list of free slots
    recurrent_free_slots: dict[int, list[Slot]]  # weekday (0-6) -> list of free slots
    schedule: dict[str, list[Event]]  # date -> list of events (lessons, vacations, breaks)
    user_settings: UserSettings


class CacheData(BaseModel):
    users: dict[int, UserCacheData]  # telegram_id -> UserCacheData
    teachers: dict[str, int]  # code -> teacher_id


class BotCache:
    """Pure storage layer - no business logic."""

    def __init__(self, maxsize: int = 100, ttl: int = 300) -> None:
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)

    def get(self, key: str) -> dict | None:
        """Get cached data by key."""
        return self._cache.get(key)

    def set(self, key: str, data: dict) -> None:
        """Store data in cache."""
        self._cache[key] = data

    def invalidate(self, key: str) -> None:
        """Remove specific cache entry."""
        self._cache.pop(key, None)

    def invalidate_all(self) -> None:
        """Clear entire cache."""
        self._cache.clear()

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return key in self._cache


# Global instance
cache = BotCache()
