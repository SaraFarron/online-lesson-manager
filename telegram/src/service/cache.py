import sys
from datetime import datetime, time

from cachetools import TTLCache
from pydantic import BaseModel


class Event(BaseModel):
    id: int
    type: str
    start: datetime | time
    is_recurrent: bool


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
    """Pure storage layer with stale fallback - no business logic."""

    def __init__(self, maxsize: int = 100, ttl: int = 300) -> None:
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._stale_cache: dict[str, dict] = {}  # Permanent storage for expired data
        self._stale_timestamps: dict[str, datetime] = {}  # Track when data became stale
        self._stale_sizes: dict[str, int] = {}  # Track memory size of stale entries (bytes)

    def get(self, key: str) -> dict | None:
        """Get cached data by key. Returns fresh cache if available, falls back to stale on TTL miss."""
        # Try fresh cache first
        data = self._cache.get(key)
        if data is not None:
            return data
        
        # Fallback to stale cache if fresh miss
        return self._stale_cache.get(key)

    def set(self, key: str, data: dict) -> None:
        """Store data in fresh cache and maintain stale copy."""
        self._cache[key] = data
        # Always keep a copy in stale cache for fallback
        self._stale_cache[key] = data
        self._stale_timestamps[key] = datetime.now()
        # Calculate and store size of stale entry
        self._stale_sizes[key] = sys.getsizeof(data)

    def invalidate(self, key: str) -> None:
        """Remove specific cache entry."""
        self._cache.pop(key, None)

    def invalidate_all(self) -> None:
        """Clear entire cache."""
        self._cache.clear()

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return key in self._cache

    def is_stale(self, key: str) -> bool:
        """Check if data is being served from stale cache (not fresh)."""
        return key not in self._cache and key in self._stale_cache

    def get_stale_age(self, key: str) -> int | None:
        """Get age of stale data in seconds. Returns None if not in stale cache."""
        if key not in self._stale_timestamps:
            return None
        age_seconds = (datetime.now() - self._stale_timestamps[key]).total_seconds()
        return int(age_seconds)

    def get_stale_memory_usage(self) -> int:
        """Get total memory used by stale cache in bytes."""
        return sum(self._stale_sizes.values())

    def get_stale_memory_usage_mb(self) -> float:
        """Get total memory used by stale cache in MB."""
        return self.get_stale_memory_usage() / (1024 * 1024)

    def prune_stale(self, max_age_seconds: int = 604_800, max_memory_bytes: int = 50_000_000) -> dict:
        """
        Prune stale cache entries based on age and memory limits.

        Args:
            max_age_seconds: Remove stale entries older than this (default: 1 week = 604800s)
            max_memory_bytes: Keep stale cache under this limit (default: 50 MB)

        Returns:
            Dict with pruning stats: {removed_by_age, removed_by_memory, current_memory_mb}

        """
        stats = {"removed_by_age": 0, "removed_by_memory": 0}

        # Prune by age
        current_time = datetime.now()
        expired_keys = [
            key
            for key, timestamp in self._stale_timestamps.items()
            if (current_time - timestamp).total_seconds() > max_age_seconds
        ]
        for key in expired_keys:
            self._stale_cache.pop(key, None)
            self._stale_timestamps.pop(key, None)
            self._stale_sizes.pop(key, None)
            stats["removed_by_age"] += 1

        # Prune by memory limit
        current_memory = self.get_stale_memory_usage()
        if current_memory > max_memory_bytes:
            # Sort by timestamp (oldest first) and remove until under limit
            sorted_keys = sorted(
                self._stale_timestamps.items(),
                key=lambda x: x[1],
            )
            for key, _ in sorted_keys:
                if self.get_stale_memory_usage() <= max_memory_bytes:
                    break
                self._stale_cache.pop(key, None)
                self._stale_timestamps.pop(key, None)
                self._stale_sizes.pop(key, None)
                stats["removed_by_memory"] += 1

        stats["current_memory_mb"] = self.get_stale_memory_usage_mb()
        return stats


# Global instance
cache = BotCache()
