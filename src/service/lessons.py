from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from core import config
from db.getters import get_cancels, get_events_for_day, get_exec_work_hours_by_user_id, get_recurrent_events, get_executor_settings_by_id
from db.models import CancelledRecurrentEvent, Event, RecurrentEvent
from db.repositories import DBSession
from db.schemas import EventSchema, RecurrentEventSchema, UserSchema
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
        lesson_before = [l for l in lessons if l.start.time() == previous_time]
        created_break = False
        if previous_time not in available_time and lesson_before and end in available_time:
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
        start = datetime.combine(day, time)
        end = datetime.combine(day, time.replace(hour=time.hour + 1))
        old_lesson = EventService(self.db).cancel_event(event_id)
        new_lesson = Event(
            user_id=user_id,
            executor_id=executor_id,
            event_type=Event.EventTypes.LESSON,
            start=start,
            end=end,
        )

        available_time, lessons = self.event_service.available_time(executor_id, day)
        previous_time = (start - timedelta(hours=1)).time()
        lesson_before = [l for l in lessons if l.start.time() == previous_time]
        created_break = False
        if previous_time not in available_time and lesson_before and end in available_time:
            work_break = Event(
                user_id=executor_id,
                executor_id=executor_id,
                event_type=RecurrentEvent.EventTypes.WORK_BREAK,
                start=end,
                end=end + timedelta(minutes=15),
            )
            self.db.add(work_break)
            created_break = datetime.strftime(end, config.TIME_FMT)

        self.db.add(new_lesson)
        self.db.commit()
        return old_lesson, new_lesson, created_break

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

        available_time, lessons = self.event_service.available_time_weekday(executor_id, start.weekday())
        now = datetime.now()
        start_of_week = now.date() - timedelta(days=now.weekday())
        current_day = start_of_week + timedelta(days=start.weekday())
        start = datetime.combine(current_day, start.time())
        end = start + config.LESSON_SIZE
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
        return old_lesson_str, lesson, created_break

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

        available_time, _ = self.event_service.available_time(recurrent_lesson.executor_id, new_date)
        if new_time not in available_time:
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


def get_available_slots(start: time, end: time, slot_size: timedelta, events: list[Event], day: date):
    current_slot = datetime.combine(day, start)
    end_of_times = datetime.combine(day, end)

    def is_occupied(slot_start: time, slot_end: time):
        return any(slot_start < occupied.end.time() and slot_end > occupied.start.time() for occupied in events)

    while current_slot + config.LESSON_SIZE <= end_of_times:
        end_slot = current_slot + config.LESSON_SIZE
        if not is_occupied(current_slot.time(), end_slot.time()):
            yield current_slot, end_slot
        current_slot += slot_size


def get_recurrent_events_for_day(db: Session, executor_id: int, day: date) -> list[RecurrentEventSchema]:
    weekday = day.weekday()
    recurrent_events = get_recurrent_events(db, executor_id)
    recurrent_events_for_day = [
        re for re in recurrent_events
        if re.start.weekday() == weekday
    ]
    event_ids = [re.id for re in recurrent_events_for_day]
    cancels = {cancel.event_id: cancel for cancel in get_cancels(db, event_ids)}
    final_recurrent_events = []
    for recurrent_event in recurrent_events_for_day:
        cancel = cancels.get(recurrent_event.id)
        if cancel and cancel.start.date() == day:
            continue
        final_recurrent_events.append(recurrent_event)
    return final_recurrent_events


def get_day_schedule(db: Session, executor_id: int, day: date) -> list[EventSchema | RecurrentEventSchema]:
    day_events = get_events_for_day(db, executor_id, day)
    day_recurrent_events = get_recurrent_events_for_day(db, executor_id, day)
    all_events = day_events + day_recurrent_events
    all_events.sort(key=lambda event: event.start)
    return all_events


def available_time_for_day(db: Session, user_id: int, day: date, executor_id: int | None = None) -> list[str]:
    if executor_id:
        executor = get_executor_settings_by_id(db, executor_id)
    else:
        executor = get_exec_work_hours_by_user_id(db, user_id)

    day_events = get_day_schedule(db, executor.id, day)    
    free_slots = list(
        get_available_slots(
            executor.work_start,
            executor.work_end,
            config.SLOT_SIZE,
            day_events,
            day,
        ),
    )
    free_slots.sort(key=lambda slot: slot[0])
    return [datetime.strftime(slot[0], config.TIME_FMT) for slot in free_slots]
