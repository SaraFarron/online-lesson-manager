from datetime import date as date_type
from datetime import datetime, time
from typing import Any

import pytz
from aiohttp import ClientError, ClientSession
from circuitbreaker import CircuitBreakerError

from src.core.config import TIMEZONE
from src.core.logger import logger
from src.schemas import EventCreate, UserCreate
from src.service.cache import Event, Slot, UserCacheData, UserSettings, cache


class BackendClient:
    """Orchestrator - handles cache strategy and backend communication."""

    API_URL = "http://localhost:8000/api/v1"
    CACHE_KEY_TEMPLATE = "schedule:{user_id}"
    _instance: "BackendClient | None" = None
    
    @staticmethod
    def moscow_to_utc(dt: datetime) -> datetime:
        """Convert Moscow datetime to UTC."""
        if dt.tzinfo is None:
            dt = TIMEZONE.localize(dt)
        return dt.astimezone(pytz.utc)
    
    @staticmethod
    def utc_to_moscow(dt: datetime) -> datetime:
        """Convert UTC datetime to Moscow timezone."""
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(TIMEZONE)
    
    @staticmethod
    def combine_date_time_moscow(day: date_type, start_time: time) -> datetime:
        """Combine date and time in Moscow timezone."""
        dt = datetime.combine(day, start_time)
        return TIMEZONE.localize(dt)

    def __new__(cls) -> "BackendClient":
        """Singleton pattern: ensure only one instance exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self.session: ClientSession | None = None
        self._initialized = True

    async def _get_session(self) -> ClientSession:
        """Lazy session initialization."""
        if self.session is None:
            self.session = ClientSession()
        return self.session

    async def _request(self, method: str, url: str, **kwargs: dict[str, Any]) -> dict | None:
        """Generic request with x service key."""
        headers = {
            "X-Service-Key": "your-secret-service-key-here",
        } | kwargs.pop("headers", {})
        try:
            session = await self._get_session()
            async with session.request(method, url, headers=headers, **kwargs) as response:
                if response.status in (200, 201):
                    return (await response.json())["data"]
                logger.warning(f"Backend returned status {response.status} for {method} {url}")
                return None
        except ClientError as e:
            logger.error(f"Network error during {method} {url}: {e}")
            return None
        except CircuitBreakerError:
            logger.warning(f"Circuit breaker open for {method} {url}")
            return None

    async def _user_request(self, method: str, url: str, token: str, **kwargs: dict[str, Any]) -> dict | None:
        """User-authenticated request."""
        headers = {
            "Authorization": f"Bearer {token}",
        } | kwargs.pop("headers", {})
        return await self._request(method, url, headers=headers, **kwargs)

    async def _fetch_from_backend(self, telegram_id: int) -> UserCacheData | None:
        """Fetch schedule from backend API and convert UTC times to Moscow timezone."""
        response = await self._request(
            "GET",
            f"{self.API_URL}/internal/schedule/{telegram_id}",
        )
        if response is not None:
            try:
                user_data = response[str(telegram_id)]
                
                # Convert UTC datetimes in schedule to Moscow timezone
                if "schedule" in user_data:
                    for events in user_data["schedule"].values():
                        for event in events:
                            if "start" in event and isinstance(event["start"], str):
                                # Check if it's a full datetime or just a time
                                if "T" in event["start"] or " " in event["start"]:
                                    # Parse UTC datetime and convert to Moscow
                                    utc_dt = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
                                    moscow_dt = self.utc_to_moscow(utc_dt)
                                    event["start"] = moscow_dt
                                else:
                                    # It's just a time string, parse as time object (no timezone conversion needed)
                                    event["start"] = time.fromisoformat(event["start"])
                
                return UserCacheData(**user_data)
            except Exception as e:
                logger.error(f"Error parsing backend response for user {telegram_id}: {e}")
                return None
        return None

    async def get_teachers(self) -> dict[str, int]:
        """Fetch teacher codes from backend."""
        response = await self._request("GET", f"{self.API_URL}/users/teachers")
        return response if response else {}

    async def get_user_cache_data(self, telegram_id: int) -> UserCacheData | None:
        """
        Read flow: Check cache → Fetch backend → Cache result → Fallback to stale.

        Args:
            telegram_id: Unique user identifier

        Returns:
            UserCacheData if found (fresh or stale), None if completely unavailable

        """
        cache_key = self.CACHE_KEY_TEMPLATE.format(user_id=telegram_id)

        # Step 1: Check if fresh cached data exists
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Cache hit for user {telegram_id}")
            return UserCacheData(**cached_data)

        # Step 2: Fetch from backend
        logger.debug(f"Cache miss for user {telegram_id}, fetching from backend")
        fresh_data = await self._fetch_from_backend(telegram_id)

        if fresh_data is not None:
            # Step 3: Cache the fresh data
            cache.set(cache_key, dict(fresh_data))
            logger.info(f"Updated cache for user {telegram_id}")
            return fresh_data

        # Step 4: Fallback to stale cache if backend unavailable
        # Note: TTLCache automatically handles expiration,
        # so we need a separate "stale cache" if you want to serve expired data
        logger.warning(f"Backend unavailable for user {telegram_id}, no cache available")
        return None

    async def user_exists(self, telegram_id: int) -> bool:
        """Check if user exists in cache or backend."""
        return bool(await self.get_user_cache_data(telegram_id))

    async def get_user(self, telegram_id: int) -> UserSettings | None:
        """Get user data."""
        data = await self.get_user_cache_data(telegram_id)
        return data.user_settings if data else None

    async def get_user_schedule(self, telegram_id: int) -> dict[str, list[Event]] | None:
        """Get user schedule."""
        data = await self.get_user_cache_data(telegram_id)
        return data.schedule if data else None

    async def get_user_free_slots(self, telegram_id: int) -> dict[str, list[Slot]] | None:
        """Get user free slots."""
        data = await self.get_user_cache_data(telegram_id)
        return data.free_slots if data else None

    async def get_user_recurrent_free_slots(self, telegram_id: int) -> dict[int, list[Slot]] | None:
        """Get user recurrent free slots."""
        data = await self.get_user_cache_data(telegram_id)
        return data.recurrent_free_slots if data else None

    async def invalidate_user(self, telegram_id: int) -> None:
        """Invalidate cache after write operations."""
        cache_key = self.CACHE_KEY_TEMPLATE.format(user_id=telegram_id)
        cache.invalidate(cache_key)
        logger.info(f"Invalidated cache for user {telegram_id}")

    async def create_user(self, user: UserCreate) -> UserSettings | None:
        """Create a new user in the backend."""
        response = await self._request(
            "POST",
            f"{self.API_URL}/auth/register",
            json={
                "telegram_id": user.telegram_id,
                "telegram_username": user.username,
                "telegram_full_name": user.full_name,
                "code": user.code,
                "role": user.role,
            },
        )
        if response is not None:
            logger.info(f"Created user {user.telegram_id} in backend")
        return UserSettings(**response) if response else None

    async def get_teacher_id(self, student_telegram_id: int) -> int | None:
        """Get teacher ID for a given student."""
        user_data = await self.get_user_cache_data(student_telegram_id)
        if user_data is None:
            logger.warning(f"Cannot get teacher ID for user {student_telegram_id}: no cache data")
            return None
        return user_data.user_settings.teacher_telegram_id

    async def create_event(self, event: EventCreate, token: str):
        # Combine date and time in Moscow timezone, then convert to UTC
        moscow_dt = self.combine_date_time_moscow(event.day, event.start)
        utc_dt = self.moscow_to_utc(moscow_dt)
        
        response = await self._user_request(
            "POST",
            f"{self.API_URL}/events",
            token=token,
            json={
                "title": event.title,
                "start": utc_dt.isoformat().replace("+00:00", "Z"),  # Format as "2026-02-10T06:00:00Z"
                "duration": event.duration,
            },
        )
        if not response:
            raise Exception("Failed to create event")
        return response

    async def update_event(self, event_id: int, event: dict, token: str):
        # TODO: When implementing, ensure datetime fields are converted to UTC using moscow_to_utc()
        # and formatted as ISO 8601 with 'Z' suffix: utc_dt.isoformat().replace('+00:00', 'Z')
        pass

    async def delete_event(self, event_id: int, token: str):
        pass

    async def create_recurrent_event(self, event: dict, token: str):
        # TODO: When implementing, ensure datetime fields are converted to UTC using moscow_to_utc()
        # and formatted as ISO 8601 with 'Z' suffix: utc_dt.isoformat().replace('+00:00', 'Z')
        pass

    async def update_recurrent_event(self, event_id: int, event: dict, token: str):
        # TODO: When implementing, ensure datetime fields are converted to UTC using moscow_to_utc()
        # and formatted as ISO 8601 with 'Z' suffix: utc_dt.isoformat().replace('+00:00', 'Z')
        pass

    async def close(self) -> None:
        """Clean up session."""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("BackendClient session closed")

    async def __aenter__(self) -> "BackendClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - ensures session cleanup."""
        await self.close()
