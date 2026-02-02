from aiohttp import ClientError, ClientSession
from circuitbreaker import CircuitBreakerError

from src.core.logger import logger
from src.service.cache import UserCacheData, UserSettings, cache


class BackendClient:
    api_url = "http://0.0.0.0:8000/api/v1"
    
    
    async def update_cache(self, user_id: int):
        """Updates cache from backend."""
        try:
            async with ClientSession() as session:
                schedule = await session.get(f"/internal/schedule/{user_id}/schedule")
                # 3. Сохраняем в кэш
                cache.set_schedule(schedule)
                return UserCacheData(**schedule.json())
        except CircuitBreakerError:
            return None
        except ClientError:
            return None
    
    async def get_cache(self):
        """
        Get schedule from cache.
        Update cache if needed.
        Returns stale if cannot update.
        """
        cached = cache.get_cache()
        if cached is not None:
            return cached
        cached = await self.update_cache()
        if cached is not None:
            return cached
        stale = cache.schedule_cache  # Даже если TTL истёк
        if stale:
            logger.warning("Returning stale schedule data")
            return stale
        return None

    # def get_user_schedule_week(self, user_id: int, start: date):
        # cached = self.get_user_schedule(user_id)
        # if cached is None:
        #     return None
        # schedule = {}
        # end = start + timedelta(days=7)
        # while start <= end:
        #     schedule[start] = self.get_user_schedule_day(start)
        #     start += timedelta(days=1)
        # return schedule

    async def set_cache(self, user_id: int, data: UserCacheData):
        # Request on backend api
        # Update cache
        cache[user_id] = data
       
    async def get_user_schedule(self, telegram_id: int):
        cached = await self.get_cache()
        if cached is not None and telegram_id in cached:
            return UserCacheData(**cached[telegram_id]).schedule
        return None

    async def get_teacher(self, code: str):
        return cache.get_teacher(code)

    async def get_user(self, telegram_id: int):
        cached = await self.get_cache()
        if cached is not None and telegram_id in cached:
            return UserCacheData(**cached[telegram_id]).user_settings
        return None
    
    async def create_user(
        self, telegram_id: int, full_name: str, username: str, role: str,
    ) -> UserSettings | None:
        async with ClientSession() as session:
            response = await session.post("/users/create", json={
                "telegram_id": telegram_id,
                "telegram_username": username,
                "telegram_full_name": full_name,
                "role": role,
            })
            if response.status == 201:
                user_data = await response.json()
                return UserSettings(**user_data)
            return None

