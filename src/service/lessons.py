from datetime import datetime, time, timedelta

from core import config
from db.models import Event, RecurrentEvent
from db.repositories import DBSession


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

    def create_recurrent_lesson(self, user_id: int, executor_id: int, weekday: int, time: time):
        now = datetime.now()
        start_of_week = now.date() - timedelta(days=now.weekday())
        current_day = start_of_week + timedelta(days=weekday)
        start = datetime.combine(current_day, time)
        lesson = RecurrentEvent(
            user_id=user_id,
            executor_id=executor_id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=start,
            end=start + config.LESSON_SIZE,
            interval=7,
        )
        self.db.add(lesson)
        self.db.commit()
        return lesson
