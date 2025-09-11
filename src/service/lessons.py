from datetime import date, datetime, time, timedelta

from core import config
from db.models import Event, RecurrentEvent
from db.repositories import DBSession
from db.schemas import UserSchema
from interface.keyboards import Keyboards
from service.services import EventService


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

    def user_lessons_buttons(self, user: UserSchema, callback: str):
        lessons = EventService(self.db).all_user_lessons(user)
        return Keyboards.choose_lesson(lessons, callback)

    def move_lesson(self, event_id: int, user_id: int, executor_id: int, day: date, time: time):
        old_lesson = EventService(self.db).cancel_event(event_id)
        new_lesson = Event(
            user_id=user_id,
            executor_id=executor_id,
            event_type=Event.EventTypes.LESSON,
            start=datetime.combine(day, time),
            end=datetime.combine(day, time.replace(hour=time.hour + 1)),
        )
        self.db.add(new_lesson)
        self.db.commit()
        return old_lesson, new_lesson

    def update_recurrent_lesson(self, event_id: int, user_id: int, executor_id: int, start: datetime):
        lesson = self.db.get(RecurrentEvent, event_id)
        old_lesson_str = str(lesson)
        if not lesson:
            msg = f"Recurrent lesson with ID {event_id} not found."
            raise ValueError(msg)

        lesson.user_id = user_id
        lesson.executor_id = executor_id
        lesson.start = start
        lesson.end = start + config.LESSON_SIZE
        lesson.interval = 7

        self.db.commit()
        return old_lesson_str, lesson
