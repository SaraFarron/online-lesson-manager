from typing import TypeVar

from cachetools import TTLCache

T = TypeVar("T")


class BotCache:
    def __init__(self):
        # Разные кэши для разных типов данных
        self.schedule_cache = TTLCache(maxsize=100, ttl=300)  # 5 минут
        self.student_lessons_cache = TTLCache(maxsize=500, ttl=120)  # 2 минуты
        self.profile_cache = TTLCache(maxsize=500, ttl=600)  # 10 минут

    def get_schedule(self, teacher_id: int) -> dict | None:
        return self.schedule_cache.get(teacher_id)

    def set_schedule(self, teacher_id: int, schedule: dict):
        self.schedule_cache[teacher_id] = schedule

    def get_student_lessons(self, student_id: int) -> list | None:
        return self.student_lessons_cache.get(student_id)

    def set_student_lessons(self, student_id: int, lessons: list):
        self.student_lessons_cache[student_id] = lessons

    def invalidate_student_lessons(self, student_id: int):
        """Вызываем после бронирования/отмены урока"""
        self.student_lessons_cache.pop(student_id, None)

    def invalidate_schedule(self, teacher_id: int):
        """Вызываем когда расписание изменилось"""
        self.schedule_cache.pop(teacher_id, None)


# Глобальный экземпляр
cache = BotCache()
