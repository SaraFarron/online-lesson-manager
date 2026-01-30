from datetime import datetime, time

from aiohttp import ClientError, ClientSession
from circuitbreaker import CircuitBreakerError
from pydantic import BaseModel

from src.core.logger import logger
from src.service.cache import cache, UserCacheData, Event, Slot

"""
Planned cache structure WIP
{
    "user_id": {
        "free_slots": {
            "01.01.2000": [["10:00", "14:00"], ["15:00", "17:00"]]  // all on month forward
        },
        "recurrent_free_slots": {
            "0": [["10:00", "14:00"], ["15:00", "17:00"]],
            "1": []
        },
        "schedule": {
            "01.01.2000": [{"type": "lesson", "start": "13:00"}]  // all on week forward
        },
        "user_settings": {}
    }
}
"""


class BackendClient:
    async def update_cache(self, teacher_id: int):
        try:
            async with ClientSession() as session:
                schedule = await session.get(f"/teachers/{teacher_id}/schedule")
                # 3. Сохраняем в кэш
                cache.set_schedule(teacher_id, schedule)
                return ScheduleData(**schedule.json())
        except CircuitBreakerError:
            return None
        except ClientError:
            return None
    
    async def get_cache(self, teacher_id: int):
        cached = cache.get_schedule(teacher_id)
        if cached is not None:
            return cached
        cached = await self.update_cache(teacher_id)
        if cached is not None:
            return cached
        stale = cache.schedule_cache  # Даже если TTL истёк
        if stale:
            logger.warning("Returning stale schedule data")
            return stale
        return None
    
    async def set_cache(self, teacher_id: int, data: UserCacheData):
        # Request on backend api
        # Update cache
        cache[teacher_id] = data
    
    async def create_event(self, teacher_id: int, event: Event):
        cached = await self.get_cache(teacher_id)
        cached.events.append(CacheEvent(
            type=event.type,
            user=event.user_id,
            start=event.start.isoformat(),
            end=event.end.isoformat(),
        ))
        await self.set_cache(teacher_id, cached)
    
    async def update_event(self, teacher_id: int, event_id: int, event: Event):
        cached = await self.get_cache(teacher_id)
        old_event = next([e for e in cached.events if e.id == event_id])
        cached.events.remove(old_event)
        cached.events.append(CacheEvent(
            type=event.type,
            user=event.user_id,
            start=event.start.isoformat(),
            end=event.end.isoformat(),
        ))
        await self.set_cache(teacher_id, cached)
    
    async def delete_event(self, teacher_id: int, event_id: int):
        cached = await self.get_cache(teacher_id)
        event = next([e for e in cached.events if e.id == event_id])
        cached.events.remove(event)
        await self.set_cache(teacher_id, cached)
    
    async def create_cancellation(self, teacher_id: int, cancel: Event):
        pass
    
    async def get_schedule(self, teacher_id: int):
        return await self.get_cache(teacher_id)

