from datetime import date, datetime, time, timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from src.core.config import DB_DATETIME, LESSON_SIZE
from src.db.models import Event, EventHistory, Executor, RecurrentEvent, User
from src.db.schemas import (
    CancelledRecurrentEventSchema,
    EventHistorySchema,
    EventSchema,
    ExecutorSchema,
    RecurrentEventSchema,
    UserSchema,
)


class DBSession:
    def __init__(self, db: Session):
        self.db = db


class UserRepo(DBSession):
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


class EventHistoryRepo(DBSession):
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
        return [EventHistorySchema.from_row(log) for log in query]


class EventRepo(DBSession):
    LESSON_TYPES = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)

    @staticmethod
    def will_overlap(recurrent_start, recurrent_end, interval_days, simple_start, simple_end):
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

    def events_executor(self, executor_id: int):
        today = datetime.now().date()
        query = self.db.execute(
            text("""
                    select * from events
                    where executor_id = :executor_id and start >= :today and cancelled is false
                    order by start
            """),
            {"executor_id": executor_id, "today": today},
        )
        return [EventSchema.from_row(event) for event in query]

    def recurrent_events_executor(self, executor_id: int):
        query = self.db.execute(
            text("""
                    select * from recurrent_events
                    where executor_id = :executor_id
                    order by start
            """),
            {"executor_id": executor_id},
        )
        return [RecurrentEventSchema.from_row(re) for re in query]

    def recurrent_events_cancels(self, events: list[RecurrentEventSchema]):
        if events:
            event_ids = [e.id for e in events]
            query = self.db.execute(text("""
                    SELECT * FROM event_breaks WHERE event_id IN :event_ids
                """).bindparams(bindparam("event_ids", expanding=True)),
                                    {"event_ids": event_ids},
            )
            return [
                CancelledRecurrentEventSchema(
                    id=cancel.id,
                    event_id=cancel.event_id,
                    break_type=cancel.break_type,
                    start=datetime.strptime(cancel.start, DB_DATETIME),
                    end=datetime.strptime(cancel.end, DB_DATETIME),
                ) for cancel in query
            ]
        return []

    def recurrent_events(self, executor_id: int):
        events = self.recurrent_events_executor(executor_id)
        cancellations = self.recurrent_events_cancels(events)
        return events, cancellations

    def recurrent_events_for_day(self, executor_id: int, day: date):
        events, cancels = self.recurrent_events(executor_id)
        result = []
        for event in events:
            # Skip if event recurrence has ended before our target date
            if event.interval_end and event.interval_end.date() < day:
                continue

            # Calculate the time difference between original start and target date
            days_diff = (day - event.start.date()).days

            # Check if this event should occur on target_date based on interval
            if event.interval > 0 and days_diff % event.interval == 0:
                # Calculate the exact datetime on target_date
                event_time = event.start.time()
                event_start = datetime.combine(day, event_time)
                event_end = event_start + (event.end - event.start)

                # Check if this occurrence is cancelled
                is_cancelled = False
                for cancel in cancels:

                    # Skip if cancellation is for a different event
                    if cancel.event_id != event.event_id:
                        continue

                    # Check if cancellation overlaps with this event occurrence
                    if (event_start < cancel.end) and (event_end > cancel.start):
                        is_cancelled = True
                        break

                if not is_cancelled:
                    result.append(event)
        return result

    def events_for_day(self, executor_id: int, day: date):
        start, end = self.get_work_start(executor_id)[0], self.get_work_end(executor_id)[0]
        day_start = datetime.combine(day, start)
        day_end = datetime.combine(day, end)
        query = self.db.execute(text("""
            select start, end, user_id, event_type, is_reschedule from events
            where executor_id = :executor_id and start >= :day_start and end <= :day_end and cancelled is false
            order by start desc
        """), {"executor_id": executor_id, "day_start": day_start, "day_end": day_end})
        return [EventSchema.from_row(event) for event in query]

    @staticmethod
    def get_available_slots(start: datetime, end: datetime, slot_size: timedelta, events: list):
        # Generate all slots (15-minute increments)
        all_slots = []
        current_slot = start

        while current_slot + LESSON_SIZE <= end:  # Check for full 1-hour availability
            all_slots.append((current_slot, current_slot + LESSON_SIZE))  # 1-hour slot
            current_slot += slot_size  # Move by 15 minutes

        # Function to check if a 1-hour slot overlaps with any occupied period
        def is_occupied(slot: tuple[datetime, datetime]):
            # The slot is available
            return any(not (slot[1] <= occupied.start or slot[0] >= occupied.end) for occupied in events)

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
        events = self.recurrent_events_executor(executor_id)
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
        events = self.recurrent_events_executor(executor_id)
        weekends = filter(
            lambda x: x.event_type == RecurrentEvent.EventTypes.WEEKEND,
            events,
        )
        return list(weekends)

    def available_work_weekdays(self, executor_id: int):
        weekends = []
        for weekend in self.weekends(executor_id):
            weekends.append(weekend.start.weekday())
        return [i for i in range(7) if i not in weekends]

    def vacations(self, user_id: int):
        query = self.db.execute(
            text("""
                select start, end, id from events
                where user_id = :user_id and event_type = :vacation and cancelled is false
            """),
            {"user_id": user_id, "vacation": Event.EventTypes.VACATION},
        )
        return [EventSchema.from_row(event) for event in query]

    def vacations_day(self, user_id: int, day: date):
        events = self.vacations(user_id)
        if not events:
            return False
        return any(event.start.date() <= day <= event.end.date() for event in events)

    def work_breaks(self, executor_id: int):
        events = self.recurrent_events_executor(executor_id)
        if events:
            events = list(filter(lambda x: x.event_type == RecurrentEvent.EventTypes.WORK_BREAK, events))
        return events
