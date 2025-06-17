from datetime import date, datetime, time, timedelta

from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from src.core.config import (
    DB_DATETIME,
    LESSON_SIZE,
)
from src.db.models import Event, EventHistory, Executor, RecurrentEvent, User

HISTORY_MAP = {
    "help": "запросил помощь",
    "added_lesson": "добавил урок",
    "deleted_one_lesson": "удалил разовый урок",
    "deleted_recur_lesson": "удалил урок",
    "delete_vacation": "удалил каникулы",
    "added_vacation": "добавил каникулы",
    "recur_lesson_deleted": "разово отменил урок",
}

class BaseSchema(BaseModel):
    id: int


class UserSchema(BaseSchema):
    telegram_id: int
    username: str | None
    full_name: str
    role: str
    executor_id: int

    roles: User.Roles = User.Roles

    @staticmethod
    def from_user(user: User):
        return UserSchema(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            executor_id=user.executor_id,
        )


class ExecutorSchema(BaseSchema):
    code: str
    telegram_id: int

    @staticmethod
    def from_executor(executor: Executor):
        return ExecutorSchema(id=executor.id, code=executor.code, telegram_id=executor.telegram_id)


class BaseEventSchema(BaseSchema):
    user_id: int
    executor_id: int
    event_type: str
    start: datetime
    end: datetime


class EventSchema(BaseEventSchema):
    cancelled: bool
    reschedule_id: int
    is_reschedule: bool


class RecurrentEventSchema(BaseEventSchema):
    interval: int
    interval_end: datetime


class CancelledRecurrentEventSchema(BaseSchema):
    event_id: int
    break_type: str
    start: datetime
    end: datetime


class EventHistorySchema(BaseSchema):
    author: str
    scene: str
    event_type: str
    event_value: str
    created_at: datetime


class Repo:
    def __init__(self, db: Session):
        self.db = db


class UserRepo(Repo):
    def get_by_telegram_id(self, telegram_id: int, raise_error: bool = False):
        """Retrieve a user by telegram id."""
        user = self.db.query(User).filter(User.telegram_id == telegram_id).first()
        if user is None and raise_error:
            raise Exception("message", "У вас нет прав на эту команду", "permission denied user is None")
        return UserSchema.from_user(user)

    def executor(self, user: User):
        executor = self.db.get(Executor, user.executor_id)
        return ExecutorSchema.from_executor(executor) if executor else None

    def executor_telegram_id(self, user: User):
        return self.executor(user).telegram_id


class EventHistoryRepo(Repo):
    def create(self, author: str, scene: str, event_type: str, event_value: str):
        log = EventHistory(
            author=author,
            scene=scene,
            event_type=event_type,
            event_value=event_value,
        )
        self.db.add(log)
        self.db.commit()

    def user_history(self, username: str):
        query = self.db.execute(
            text("""
                select * from event_history
                where author = :author
                order by created_at desc
                limit 10
            """),
            {"author": username},
        )
        return [
            EventHistorySchema(
                id=log.id,
                created_at=datetime.strptime(log.created_at, DB_DATETIME),
                scene=log.scene,
                event_type=log.event_type,
                event_value=log.event_value,
                author=log.author,
            ) for log in query
        ]


class EventRepo(Repo):
    LESSON_TYPES = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)

    @staticmethod
    def _will_overlap(recurrent_start, recurrent_end, interval_days, simple_start, simple_end):
        now = datetime.now()
        if simple_start < now:
            return False
        occurrence_start = recurrent_start
        occurrence_end = recurrent_end
        interval = timedelta(days=interval_days)
        while occurrence_start < simple_end and occurrence_start < now + timedelta(days=31):
            if occurrence_start < simple_end and occurrence_end > simple_start:
                return True
            occurrence_start += interval
            occurrence_end += interval
        return False

    def _events_executor(self, executor_id: int):
        today = datetime.now().date()
        query = self.db.execute(
            text("""
                    select * from events
                    where executor_id = :executor_id and start >= :today and cancelled is false
                    order by start
            """),
            {"executor_id": executor_id, "today": today},
        )
        return [
            EventSchema(
                id=event.id,
                start=datetime.strptime(event.start, DB_DATETIME),
                end=datetime.strptime(event.end, DB_DATETIME),
                user_id=event.user_id,
                event_type=event.event_type,
                is_reschedule=event.is_reschedule,
                executor_id=event.executor_id,
                cancelled=event.cancelled,
                reschedule_id=event.reschedule_id,
            ) for event in query
        ]

    def _recurrent_events_executor(self, executor_id: int):
        query = self.db.execute(
            text("""
                    select * from recurrent_events
                    where executor_id = :executor_id
                    order by start
            """),
            {"executor_id": executor_id},
        )
        return [
            RecurrentEventSchema(
                id=re.id,
                user_id=re.user_id,
                executor_id=re.executor_id,
                event_type=re.event_type,
                start=datetime.strptime(re.start, DB_DATETIME),
                end=datetime.strptime(re.end, DB_DATETIME),
                interval=re.interval,
                interval_end=re.interval_end,
            ) for re in query
        ]

    # --- TODO ---

    def recurrent_events_cancels(self, events: list[tuple]):
        if events:
            event_ids = [e[-1] for e in events]
            stmt = text("""
                SELECT event_id, break_type, start, end 
                FROM event_breaks
                WHERE event_id IN :event_ids
            """).bindparams(bindparam("event_ids", expanding=True))
            return list(self.db.execute(stmt, {"event_ids": event_ids}))
        return []

    def recurrent_events(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        cancellations = self.recurrent_events_cancels(events)
        return events, cancellations

    def recurrent_events_for_day(self, executor_id: int, day: date):
        events, cancels = self.recurrent_events(executor_id)
        result = []
        for event in events:
            start_dt, end_dt, user_id, event_type, interval, interval_end, event_id = event
            start_dt = datetime.strptime(start_dt, DB_DATETIME)
            end_dt = datetime.strptime(end_dt, DB_DATETIME)

            # Skip if event recurrence has ended before our target date
            if interval_end and interval_end.date() < day:
                continue

            # Calculate the time difference between original start and target date
            days_diff = (day - start_dt.date()).days

            # Check if this event should occur on target_date based on interval
            if interval > 0 and days_diff % interval == 0:
                # Calculate the exact datetime on target_date
                event_time = start_dt.time()
                event_start = datetime.combine(day, event_time)
                event_end = event_start + (end_dt - start_dt)

                # Check if this occurrence is cancelled
                is_cancelled = False
                for cancel in cancels:
                    c_event_id, break_type, c_start, c_end = cancel
                    c_start = datetime.strptime(c_start, DB_DATETIME)
                    c_end = datetime.strptime(c_end, DB_DATETIME)

                    # Skip if cancellation is for a different event
                    if c_event_id != event_id:
                        continue

                    # Check if cancellation overlaps with this event occurrence
                    if (event_start < c_end) and (event_end > c_start):
                        is_cancelled = True
                        break

                if not is_cancelled:
                    result.append((event_start, event_end, user_id, event_type, False))
        return result

    def events_for_day(self, executor_id: int, day: date):
        start, end = self.get_work_start(executor_id)[0], self.get_work_end(executor_id)[0]
        day_start = datetime.combine(day, start)
        day_end = datetime.combine(day, end)
        events = self.db.execute(text("""
            select start, end, user_id, event_type, is_reschedule from events
            where executor_id = :executor_id and start >= :day_start and end <= :day_end and cancelled is false
            order by start desc
        """), {"executor_id": executor_id, "day_start": day_start, "day_end": day_end})
        result = []
        for e in events:
            start_dt = datetime.strptime(e[0], DB_DATETIME)
            end_dt = datetime.strptime(e[1], DB_DATETIME)
            result.append((start_dt, end_dt, *e[2:]))
        return result

    @staticmethod
    def _get_available_slots(start: datetime, end: datetime, slot_size: timedelta, events: list):
        # Generate all slots (15-minute increments)
        all_slots = []
        current_slot = start

        while current_slot + LESSON_SIZE <= end:  # Check for full 1-hour availability
            all_slots.append((current_slot, current_slot + LESSON_SIZE))  # 1-hour slot
            current_slot += slot_size  # Move by 15 minutes

        # Function to check if a 1-hour slot overlaps with any occupied period
        def is_occupied(slot):
            slot_start, slot_end = slot
            for occupied in events:
                occupied_start = (
                    datetime.strptime(occupied[0], DB_DATETIME) if isinstance(occupied[0], str) else occupied[0]
                )
                occupied_end = (
                    datetime.strptime(occupied[1], DB_DATETIME) if isinstance(occupied[1], str) else occupied[1]
                )
                if not (slot_end <= occupied_start or slot_start >= occupied_end):
                    return True  # The slot is occupied
            return False  # The slot is available

        # Filter out occupied slots
        return [slot for slot in all_slots if not is_occupied(slot)]

    def cancel_event(self, event_id: int):
        event = self.db.get(Event, event_id)
        if event:
            event.cancelled = True
            self.db.add(event)
            self.db.commit()
            return event
        raise Exception("message", "Урок не найден", f"event with id {event_id} does not exist")

    def work_hours(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        work_hours = filter(
            lambda x: x.event_type in (RecurrentEvent.EventTypes.WORK_START, RecurrentEvent.EventTypes.WORK_END),
            events,
        )
        return list(work_hours)

    def delete_work_hour_setting(self, executor_id: int, kind: str):
        if kind == "end":
            event_time, event = self.get_work_end(executor_id)
        elif kind == "start":
            event_time, event = self.get_work_start(executor_id)
        else:
            raise Exception("message", "Неизвестный тип события", f"unknown kind: {kind}")
        self.db.delete(event)
        self.db.commit()
        return event_time

    def get_work_end(self, executor_id: int):
        event = self.db.query(RecurrentEvent).filter(
            RecurrentEvent.executor_id == executor_id,
            RecurrentEvent.event_type == RecurrentEvent.EventTypes.WORK_END,
        ).first()
        if event:
            return event.start.time(), event
        return time(hour=20, minute=0), None

    def get_work_start(self, executor_id: int):
        event = self.db.query(RecurrentEvent).filter(
            RecurrentEvent.executor_id == executor_id,
            RecurrentEvent.event_type == RecurrentEvent.EventTypes.WORK_START,
        ).first()
        if event:
            return event.end.time(), event
        return time(hour=9, minute=0), None

    def weekends(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        weekends = filter(
            lambda x: x.event_type == RecurrentEvent.EventTypes.WEEKEND,
            events,
        )
        return list(weekends)

    def available_work_weekdays(self, executor_id: int):
        weekends = []
        for weekend in self.weekends(executor_id):
            if not isinstance(weekend.start, datetime):
                start = datetime.strptime(weekend.start, DB_DATETIME)
            else:
                start = weekend.start
            weekends.append(start.weekday())
        return [i for i in range(7) if i not in weekends]

    def vacations(self, user_id: int):
        events = self.db.execute(
            text("""
                select start, end, id from events
                where user_id = :user_id and event_type = :vacation and cancelled is false
            """),
            {"user_id": user_id, "vacation": Event.EventTypes.VACATION},
        )
        return list(events)

    def vacations_day(self, user_id: int, day: date):
        events = self.vacations(user_id)
        if not events:
            return False
        for event in events:
            start = datetime.strptime(event.start, DB_DATETIME)
            end = datetime.strptime(event.end, DB_DATETIME)
            if start.date() <= day <= end.date():
                return True
        return False

    def work_breaks(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        if events:
            events = list(filter(lambda x: x.event_type == RecurrentEvent.EventTypes.WORK_BREAK, events))
        return events
