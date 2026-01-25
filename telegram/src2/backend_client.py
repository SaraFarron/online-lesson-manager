import httpx
from circuitbreaker import circuit

from .cache import BotCache


class BackendUnavailable(Exception):
    pass


class BackendClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=5)

    # Circuit Breaker:
    # - 5 ошибок подряд → OPEN
    # - через 60 сек → HALF-OPEN
    @circuit(failure_threshold=5, recovery_timeout=60)
    async def _request(self, method: str, url: str, **kwargs):
        response = await self.client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    # ---------- READ ----------
    async def get_schedule(self, teacher_id: int) -> dict | None:
        cache_key = f"teacher:{teacher_id}"

        # 1. Есть свежий кэш → сразу отдаём
        if cache_key in BotCache.schedule:
            return BotCache.schedule[cache_key]

        try:
            data = await self._request(
                "GET",
                f"{self.base_url}/teachers/{teacher_id}/schedule",
            )
            BotCache.schedule[cache_key] = data
            return data

        except Exception:
            # 2. Backend недоступен → пробуем stale
            if cache_key in BotCache.schedule:
                stale = BotCache.schedule[cache_key].copy()
                stale["_stale"] = True
                return stale

            return None

    # ---------- WRITE ----------
    async def book_lesson(self, lesson_id: int, student_id: int) -> bool:
        try:
            await self._request(
                "POST",
                f"{self.base_url}/lessons/{lesson_id}/book",
                json={"student_id": student_id},
            )

            # INVALIDATE CACHE
            BotCache.schedule.clear()
            BotCache.student_lessons.clear()
            return True

        except Exception as e:
            raise BackendUnavailable from e
