from datetime import date, datetime, timedelta, time
from typing import Iterable
from sqlalchemy import or_
from sqlalchemy.orm import Session
from config.config import MAX_BUTTON_ROWS
from src.db.models import Event, User, Executor, RecurrentEvent
from src.db.repositories import EventRepo, RecurrentEventRepo
from aiogram.utils.keyboard import InlineKeyboardBuilder


class Keyboards:
    @classmethod
    def inline_keyboard(cls, buttons: dict[str, str] | Iterable[tuple[str, str]], as_markup=True):
        """Create an inline keyboard."""
        builder = InlineKeyboardBuilder()
        if isinstance(buttons, dict):
            for callback_data, text in buttons.items():
                builder.button(text=text, callback_data=callback_data)  # type: ignore  # noqa: PGH003
        else:
            for text, callback_data in buttons:
                builder.button(text=text, callback_data=callback_data)
        builder.adjust(1 if len(buttons) <= MAX_BUTTON_ROWS else 2, repeat=True)
        if as_markup:
            return builder.as_markup()
        return builder

    @classmethod
    def choose_lesson_type(cls, recurrent_type_callback: str, single_type_callback: str):
        buttons = {
            recurrent_type_callback: "Еженедельное занятие",
            single_type_callback: "Одноразовое занятие"
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def weekdays(cls, days: list[int], callback: str, short=False):
        buttons = {}
        for day in days:
            match day:
                case 0: buttons[callback + "/0"] = "ПН" if short else "Понедельник"
                case 1: buttons[callback + "/1"] = "ВТ" if short else "Вторник"
                case 2: buttons[callback + "/2"] = "СР" if short else "Среда"
                case 3: buttons[callback + "/3"] = "ЧТ" if short else "Четверг"
                case 4: buttons[callback + "/4"] = "ПТ" if short else "Пятница"
                case 5: buttons[callback + "/5"] = "СБ" if short else "Суббота"
                case 6: buttons[callback + "/6"] = "ВС" if short else "Воскресенье"
        return cls.inline_keyboard(buttons)

    @classmethod
    def choose_time(cls, times: list[time], callback: str):
        buttons = {callback + str(t): str(t) for t in times}
        return cls.inline_keyboard(buttons)

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


class Service:
    def __init__(self, db: Session):
        self.db = db
        self.event_service = EventService(db)

    def get_user(self, telegram_id: int):
        pass

    def events_to_cancel(self, user: User, day: date):
        pass

    def available_weekdays(self, user: User):
        for wd in range(6):
            free = self.event_service.is_available_weekday(user, wd)
            if free:
                yield wd
