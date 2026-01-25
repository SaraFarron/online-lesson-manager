from cachetools import TTLCache


class BotCache:
    """
    Храним только READ-данные.
    Значения могут быть помечены как stale.
    """
    schedule = TTLCache(maxsize=100, ttl=300)          # 5 минут
    student_lessons = TTLCache(maxsize=500, ttl=120)  # 2 минуты
