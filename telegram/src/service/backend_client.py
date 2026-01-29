from datetime import date, datetime

from aiohttp import ClientError, ClientSession
from circuitbreaker import CircuitBreakerError
from pydantic import BaseModel

from src.core.logger import logger
from src.service.cache import cache

"""
Planned cache structure WIP
{
    "teacher_1": {
        "events": {
            "01.01.2000": [
                {
                    "type": "lesson",
                    "user": "telegram_id",
                    "start": "12:00"
                    "duration": 60
                }
            ]
        },
        "users": {
            "telegram_id": {
                "username": "username",
                "full_name": "name"
            }
        }
    }
}
"""

class Event(BaseModel):
    type: str
    user: int
    start: datetime


class BackendClient:
    async def update_cache(self, teacher_id: int):
        try:
            async with ClientSession() as session:
                schedule = await session.get(f"/teachers/{teacher_id}/schedule")
                # 3. Сохраняем в кэш
                cache.set_schedule(teacher_id, schedule)
                return schedule
        except CircuitBreakerError:
            return None
        except ClientError:
            return None
    
    def create_event(self, teacher_id: int, student_id: int, event: Event):
        pass
    
    def update_event(self, teacher_id: int, student_id: int, event_id: int, event: Event):
        pass
    
    def delete_event(self, teacher_id: int, student_id: int, event_id: int):
        pass
    
    def create_cancellation(self, teacher_id: int, student_id: int, cancel: Event):
        pass
    
    async def get_schedule(self, teacher_id: int, day: date):
        cached = cache.get_schedule(teacher_id)
        if cached is not None:
            return cached["events"].get(str(day), [])
        cached = await self.update_cache(teacher_id)
        if cached is not None:
            return cached["events"].get(str(day), [])
        # 4. Circuit открыт — пробуем вернуть устаревшие данные
        #    (лучше старое расписание чем ничего)
        stale = cache.schedule_cache.get(teacher_id)  # Даже если TTL истёк
        if stale:
            logger.warning("Returning stale schedule data")
            return stale["events"].get(str(day), [])
        return None
