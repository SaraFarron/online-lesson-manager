from datetime import datetime

from core import config
from db.models import Event
from src.db.repositories import DBSession


class LessonsService(DBSession):
    def create_lesson(self, user_id: int, executor_id: int, date: str, time: str) -> Event:
        ttime = datetime.strptime(time, config.TIME_FMT).time()
        lesson = Event(
            user_id=user_id,
            executor_id=executor_id,
            event_type=Event.EventTypes.LESSON,
            start=datetime.combine(date, ttime),
            end=datetime.combine(date, ttime.replace(hour=ttime.hour + 1)),
        )
        self.db.add(lesson)
        self.db.commit()
        return lesson
