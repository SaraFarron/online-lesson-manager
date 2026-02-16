from datetime import date as date_type
from datetime import datetime, time
from typing import Any

import pytz
from aiohttp import ClientSession
from circuitbreaker import circuit

from src.core.config import TIMEZONE
from src.core.logger import logger
from src.schemas import EventCreate, UserCreate
from src.service.cache import Event, Slot, UserCacheData, UserSettings, cache


class BackendClientError(Exception):
    """Custom exception for BackendClient errors."""

    def __init__(self, detail: str, status: int) -> None:
        self.detail = detail
        self.status = status
        super().__init__(detail)


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

    @staticmethod
    def convert_time_utc_to_moscow(utc_time: time) -> time:
        """
        Convert a time object from UTC to Moscow timezone.

        Uses a reference date to handle timezone conversion properly.
        Note: This may shift to previous/next day for times near midnight.
        """
        reference_date = date_type(2026, 1, 1)  # Arbitrary reference date
        utc_dt = datetime.combine(reference_date, utc_time)
        utc_dt = pytz.utc.localize(utc_dt)
        moscow_dt = utc_dt.astimezone(TIMEZONE)
        return moscow_dt.time()

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

    async def _request(self, method: str, url: str, **kwargs: dict[str, Any]):
        """Generic request with x service key."""
        headers = {
            "X-Service-Key": "your-secret-service-key-here",
        } | kwargs.pop("headers", {})
        session = await self._get_session()
        async with session.request(method, url, headers=headers, **kwargs) as response:
            if response.status >= 400:
                data = await response.json()
                if "error" in data:
                    error_message = data["error"].get("message", "Произошла неизвестная ошибка")
                    raise BackendClientError(error_message, response.status)
                raise BackendClientError("Произошла неизвестная ошибка", response.status)
            if 199 < response.status < 300:
                if response.status == 204:
                    return None
                data = await response.json()
                if "data" in data:  # TODO error handling
                    return data["data"]
                return response.status
            logger.warning(f"Backend returned status {response.status} for {method} {url}")
            return response.status

    async def _user_request(self, method: str, url: str, token: str, **kwargs: dict[str, Any]):
        """User-authenticated request."""
        headers = {
            "Authorization": f"Bearer {token}",
        } | kwargs.pop("headers", {})
        return await self._request(method, url, headers=headers, **kwargs)

    def _convert_slot_timezone(self, slot: dict) -> dict:
        """Convert slot times from UTC to Moscow timezone."""
        if "start" in slot and isinstance(slot["start"], str):
            utc_time = time.fromisoformat(slot["start"])
            slot["start"] = self.convert_time_utc_to_moscow(utc_time)
        if "end" in slot and isinstance(slot["end"], str):
            utc_time = time.fromisoformat(slot["end"])
            slot["end"] = self.convert_time_utc_to_moscow(utc_time)
        return slot

    def _convert_user_data_event_times(self, user_data: dict) -> dict:
        """Convert all event times in user_data from UTC to Moscow timezone."""
        assert all(key in user_data for key in ("user_settings", "free_slots", "recurrent_free_slots", "schedule")), (
            "Invalid user_data structure"
        )

        for key, slots in user_data["free_slots"].items():
            user_data["free_slots"][key] = [self._convert_slot_timezone(slot) for slot in slots]

        for key, slots in user_data["recurrent_free_slots"].items():
            user_data["recurrent_free_slots"][key] = [self._convert_slot_timezone(slot) for slot in slots]

        for events in user_data["schedule"].values():
            for event in events:
                if not ("start" in event and isinstance(event["start"], str)):
                    continue
                # Check if it's a full datetime or just a time
                if "T" in event["start"] or " " in event["start"]:
                    # Parse UTC datetime and convert to Moscow
                    utc_dt = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
                    moscow_dt = self.utc_to_moscow(utc_dt)
                    event["start"] = moscow_dt
                else:
                    # It's just a time string, convert to Moscow time
                    utc_time = time.fromisoformat(event["start"])
                    event["start"] = self.convert_time_utc_to_moscow(utc_time)
        return user_data

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def _fetch_from_backend(self, telegram_id: int) -> UserCacheData | None:
        """Fetch schedule from backend API and convert UTC times to Moscow timezone."""
        response = await self._request(
            "GET",
            f"{self.API_URL}/internal/schedule/{telegram_id}",
        )
        if response is not None:
            try:
                user_data = response[str(telegram_id)]
                user_data = self._convert_user_data_event_times(user_data)
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

        Stale fallback is automatic:
        1. Check fresh cache (TTL 5min)
        2. Fetch from backend on miss
        3. Cache fresh data + maintain stale copy
        4. If backend unavailable, cache.get() auto-serves stale data

        Args:
            telegram_id: Unique user identifier

        Returns:
            UserCacheData if found (fresh or stale), None if completely unavailable

        """
        cache_key = self.CACHE_KEY_TEMPLATE.format(user_id=telegram_id)

        # Step 1: Try to get from cache (fresh or stale fallback)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            # Check if this is stale data (being served as fallback)
            if cache.is_stale(cache_key):
                stale_age = cache.get_stale_age(cache_key)
                logger.warning(
                    f"Cache miss for user {telegram_id}, serving stale data (age: {stale_age}s). "
                    f"Backend unavailable or circuit breaker open.",
                )
            else:
                logger.debug(f"Fresh cache hit for user {telegram_id}")
            return UserCacheData(**cached_data)

        # Step 2: Fetch from backend
        logger.debug(f"Cache miss for user {telegram_id}, fetching from backend")
        try:
            fresh_data = await self._fetch_from_backend(telegram_id)
        except Exception as e:
            logger.error(f"Backend fetch failed for user {telegram_id}: {e}. Circuit breaker may be open.")
            fresh_data = None

        if fresh_data is not None:
            # Step 3: Cache the fresh data (also maintains stale copy)
            cache.set(cache_key, dict(fresh_data))
            logger.info(f"Updated cache for user {telegram_id}")
            return fresh_data

        # Step 4: Fallback to stale cache (automatic via cache.get())
        stale_data = cache.get(cache_key)
        if stale_data is not None:
            stale_age = cache.get_stale_age(cache_key)
            logger.warning(
                f"Backend unavailable for user {telegram_id}, serving stale data (age: {stale_age}s). "
                f"Circuit breaker may be protecting against cascade failures.",
            )
            return UserCacheData(**stale_data)

        logger.warning(f"Backend unavailable for user {telegram_id}, no cache available (fresh or stale)")
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
        """Invalidate fresh cache after write operations. Stale copy is preserved for fallback."""
        cache_key = self.CACHE_KEY_TEMPLATE.format(user_id=telegram_id)
        cache.invalidate(cache_key)
        logger.info(f"Invalidated fresh cache for user {telegram_id}. Stale copy preserved for fallback.")

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

        return await self._user_request(
            "POST",
            f"{self.API_URL}/events",
            token=token,
            json={
                "title": event.title,
                "start": utc_dt.isoformat().replace("+00:00", "Z"),  # Format as "2026-02-10T06:00:00Z"
                "duration": event.duration,
                "isRecurring": event.is_recurrent,
            },
        )

    async def update_event(self, event_id: int, event: EventCreate, token: str):
        # Combine date and time in Moscow timezone, then convert to UTC
        moscow_dt = self.combine_date_time_moscow(event.day, event.start)
        utc_dt = self.moscow_to_utc(moscow_dt)

        return await self._user_request(
            "PUT",
            f"{self.API_URL}/events/{event_id}",
            token=token,
            json={
                "title": event.title,
                "start": utc_dt.isoformat().replace("+00:00", "Z"),  # Format as "2026-02-10T06:00:00Z"
                "duration": event.duration,
                "isRecurring": event.is_recurrent,
            },
        )

    async def delete_event(self, event_id: int, token: str):
        return await self._user_request(
            "DELETE",
            f"{self.API_URL}/events/{event_id}",
            token=token,
        )

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
