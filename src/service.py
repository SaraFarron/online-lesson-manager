from datetime import date, datetime, timedelta, time
from typing import Iterable
from sqlalchemy import or_
from sqlalchemy.orm import Session
from utils import inline_keyboard
from src.models import Event, User, Executor, RecurrentEvent
from src.repositories import EventRepo, RecurrentEventRepo


class EventService:
    def __init__(self, db: Session):
        self.db = db

    def events_for_day(self, day: date, executor: Executor, user: User | None = None):
        filters = {"executor_id": executor.id, "date": day}
        if user:
            filters |= {"user_id": user.id}
        events: list[Event] = self.db.query(Event).filter_by(cancelled=False, **filters).all()

        filters.pop("date")

        day_start, day_end = datetime.combine(day, datetime.min.time()), datetime.combine(day, datetime.max.time())
        schedule: list[RecurrentEvent] = self.db.query(RecurrentEvent).filter_by(**filters).all()
        recurrent_events = []
        for re in schedule:
            if re.get_next_occurrence(day_start, day_end):
                recurrent_events.append(re)
        return events + recurrent_events

    def events_for_period(self, start: datetime, end: datetime, executor: Executor, user: User | None = None):
        event_time_condition = or_(
            (Event.date + Event.start_time) >= start,
            (Event.date + Event.end_time) <= end
        )
        if user:
            events = self.db.query(Event).filter(
                Event.executor_id == executor.id,
                Event.user_id == user.id,
                Event.cancelled == False,
                event_time_condition
            ).all()
            recurrent_events = self.db.query(RecurrentEvent).filter(
                RecurrentEvent.executor_id == executor.id,
                RecurrentEvent.user_id == user.id,
                RecurrentEvent.start >= start,
                RecurrentEvent.end <= end,
            ).all()
        else:
            events = self.db.query(Event).filter(
                Event.executor_id == executor.id, Event.cancelled == False, event_time_condition
            ).all()
            recurrent_events = self.db.query(RecurrentEvent).filter(
                RecurrentEvent.executor_id == executor.id,
                RecurrentEvent.start >= start,
                RecurrentEvent.end <= end,
            ).all()

        return events + recurrent_events

    def add_event(
            self,
            user: User,
            executor: Executor,
            event_type: str,
            start_time: time,
            end_time: time,
            day: date,
            interval: timedelta | None = None,
            start: datetime | None = None,
            end: datetime | None = None
    ):
        if not self._slot_is_free(start_time, end_time, day, executor):
            raise ValueError("Slot is occupied")
        if interval:
            return RecurrentEventRepo(self.db).new(
                user, executor, event_type, start_time, end_time, day, interval, start, end
            )
        return EventRepo(self.db).new(user, executor, event_type, start_time, end_time, day)

    @staticmethod
    def cancel_event(event: Event):
        event.cancelled = True
        return event

    @staticmethod
    def move_event(
            event: Event | RecurrentEvent,
            new_st: time | None = None,
            new_et: time | None = None,
            new_interval: timedelta | None = None,
            new_start: datetime | None = None,
            new_end: datetime | None = None
    ):
        if new_st and new_et:
            assert new_st < new_et
            if isinstance(event, Event):
                event.start_time = new_st
                event.end_time = new_et
                return event
            event.event.start_time = new_st
            event.event.end_time = new_et

        event.interval = new_interval if new_interval else event.interval
        event.start = new_start if new_start else event.start
        event.end = new_end if new_end else event.end
        if new_start or new_end:
            assert event.start < event.end
        return event

    @staticmethod
    def _get_available_slots(start: datetime, end: datetime, slot_size: timedelta, events: list):
        # Generate all slots
        all_slots = []
        current_slot = start

        while current_slot + slot_size <= end:
            all_slots.append((current_slot, current_slot + slot_size))
            current_slot += slot_size  # Move to the next slot

        # Function to check if a slot overlaps with any occupied period
        def is_occupied(slot):
            slot_start, slot_end = slot
            for occupied in events:
                occupied_start, occupied_end = occupied
                if not (slot_end <= occupied_start or slot_start >= occupied_end):
                    return True  # The slot is occupied
            return False  # The slot is available

        # Filter out occupied slots
        return [slot for slot in all_slots if not is_occupied(slot)]

    def available_slots(self, executor: Executor, start: datetime, end: datetime, slot_size: timedelta):
        events = self.events_for_period(start, end, executor)
        busy_slots = [(datetime.combine(e.date, e.start_time), datetime.combine(e.date, e.end_time)) for e in events]
        return self._get_available_slots(start, end, slot_size, busy_slots)

    def _slot_is_free(self, start_time: time, end_time: time, day: date, executor: Executor):
        start = datetime.combine(day, start_time)
        end = datetime.combine(day, end_time)
        events = self.events_for_period(start, end, executor)
        return not bool(events)


class KeyboardFactory:
    def callbacks(self, buttons: dict[str, str] | Iterable[tuple[str, str]]):
        return inline_keyboard(buttons)


class Service:
    def __init__(self, db: Session):
        self.db = db

    def get_user(self, telegram_id: int):
        """
        Get user by telegram id. If no user exists, raise PermissionError.
        Returns User
        """

    def events_to_cancel(self, user: User, day: date):
        """
        Get all cancellable events for a user for a day.
        """

    def

