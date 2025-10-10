from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from core import config
from db.models import CancelledRecurrentEvent, Event, RecurrentEvent
from db.repositories import DBSession
from db.schemas import UserSchema
from interface.keyboards import Keyboards
from service.services import EventService


class LessonsService(DBSession):
    def __init__(self, db: Session) -> None:
        super().__init__(db)
        self.event_service = EventService(db)

    def create_lesson(self, user_id: int, executor_id: int, date: date, time: str):
        available_time, lessons = self.event_service.available_time(executor_id, date)
        ttime = datetime.strptime(time, config.TIME_FMT).time()
        start = datetime.combine(date, ttime)
        if start not in available_time:
            msg = "Lesson overlaps with an existing event"
            raise ValueError(msg)

        end = start + timedelta(hours=1)
        lesson = Event(
            user_id=user_id,
            executor_id=executor_id,
            event_type=Event.EventTypes.LESSON,
            start=start,
            end=end,
        )
        self.db.add(lesson)
        previous_time = (start - timedelta(hours=1)).time()
        lesson_before = [l for l in lessons if l.start == previous_time]
        created_break = False
        if previous_time not in available_time and lesson_before and end.time() in available_time:
            work_break = Event(
                user_id=executor_id,
                executor_id=executor_id,
                event_type=RecurrentEvent.EventTypes.WORK_BREAK,
                start=end,
                end=end + timedelta(minutes=15),
            )
            self.db.add(work_break)
            created_break = datetime.strftime(end, config.TIME_FMT)
        self.db.commit()
        return lesson, created_break

    def create_recurrent_lesson(self, user_id: int, executor_id: int, weekday: int, time: time):
        # Get lessons for this weekday
        available_time, lessons = self.event_service.available_time_weekday(executor_id, weekday)
        now = datetime.now()
        start_of_week = now.date() - timedelta(days=now.weekday())
        current_day = start_of_week + timedelta(days=weekday)
        start = datetime.combine(current_day, time)
        end = start + config.LESSON_SIZE
        lesson = RecurrentEvent(
            user_id=user_id,
            executor_id=executor_id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=start,
            end=end,
            interval=7,
        )
        self.db.add(lesson)
        created_break = False
        previous_time = (start - timedelta(hours=1)).time()
        lesson_before = [l for l in lessons if l.start.time() == previous_time]
        if previous_time not in available_time and lesson_before and end in available_time:
            work_break = RecurrentEvent(
                user_id=executor_id,
                executor_id=executor_id,
                event_type=RecurrentEvent.EventTypes.WORK_BREAK,
                start=end,
                end=end + timedelta(minutes=15),
                interval=7,
            )
            self.db.add(work_break)
            created_break = datetime.strftime(end, config.TIME_FMT)
        self.db.commit()
        return lesson, created_break

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

    def move_recurrent_lesson_once(self, event_id: int, cancel_date: date, new_date: date, new_time: time):
        recurrent_lesson = self.db.get(RecurrentEvent, event_id)
        if not recurrent_lesson:
            msg = f"Recurrent lesson with ID {event_id} not found."
            raise ValueError(msg)

        cancel_start = datetime.combine(
            cancel_date,
            time(hour=recurrent_lesson.start.hour, minute=recurrent_lesson.start.minute),
        )
        cancel = self.db.query(CancelledRecurrentEvent).filter(
            CancelledRecurrentEvent.event_id == event_id,
            CancelledRecurrentEvent.start == cancel_start,
        ).first()

        if cancel:
            msg = f"Recurrent lesson already canceled for date {cancel_date}."
            raise ValueError(msg)

        if new_time not in self.event_service.available_time(recurrent_lesson.executor_id, new_date):
            msg = "New lesson time overlaps with an existing event."
            raise ValueError(msg)

        cancelled_recurrent_event = CancelledRecurrentEvent(
            event_id=event_id,
            break_type=CancelledRecurrentEvent.CancelTypes.LESSON_CANCELED,
            start=cancel_start,
            end=cancel_start + config.LESSON_SIZE,
        )
        self.db.add(cancelled_recurrent_event)

        new_lesson_start = datetime.combine(
            new_date,
            time(hour=new_time.hour, minute=new_time.minute),
        )
        new_lesson = Event(
            user_id=recurrent_lesson.user_id,
            executor_id=recurrent_lesson.executor_id,
            event_type=Event.EventTypes.LESSON,
            start=new_lesson_start,
            end=new_lesson_start + config.LESSON_SIZE,
        )
        self.db.add(new_lesson)
        self.db.commit()
        return cancelled_recurrent_event, new_lesson
