from datetime import date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.config import DB_DATETIME
from src.models import Event, EventHistory, Executor, RecurrentEvent, User


class Repo:
    def __init__(self, db: Session):
        self.db = db


class UserRepo(Repo):
    @property
    def roles(self):
        return User.Roles

    def get_by_telegram_id(self, telegram_id: int, raise_error: bool = False):
        """Retrieve a user by telegram id."""
        user = self.db.query(User).filter(User.telegram_id == telegram_id).first()
        if user is None and raise_error:
            raise Exception("message", "У вас нет прав на эту команду", "permission denied user is None")
        return user

    def register(self, tg_id: int, tg_full_name: str, tg_username: str, role: str, code: str):
        """Register a user."""
        event_log = EventHistory(
            author=tg_username,
            scene="start",
            event_type="register",
            event_value=f"tg_id: {tg_id}, tg_full_name: {tg_full_name}, tg_username: {tg_username}, role: {role}, executor: {code}",
        )

        executor = self.db.query(Executor).filter(Executor.code == code).first()
        if executor is None:
            raise Exception("message", "Произошла ошибка, скорее всего ссылка на бота неверна", "executor is None")

        user = User(
            telegram_id=tg_id,
            username=tg_username,
            full_name=tg_full_name,
            role=role,
            executor_id=executor.id,
        )
        self.db.add_all([user, event_log])
        self.db.commit()


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
        events = self.db.execute(
            text("""
                select created_at, scene, event_type, event_value from event_history
                where author = :author
                order by created_at desc
                limit 10
            """),
            {"author": username}
        )
        return list(events)


class EventRepo(Repo):
    LESSON_TYPES = (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON)

    def _events_executor(self, executor_id: int):
        today = datetime.now().date()
        return list(
            self.db.execute(
                text("""
                        select start, end, user_id, event_type, is_reschedule, id from events
                        where executor_id = :executor_id and start >= :today and cancelled is false
                        order by start
                """),
                {"executor_id": executor_id, "today": today},
            ),
        )

    def _recurrent_events_executor(self, executor_id: int):
        return list(
            self.db.execute(
                text("""
                        select start, end, user_id, event_type, interval, interval_end, id from recurrent_events
                        where executor_id = :executor_id
                        order by start
                """),
                {"executor_id": executor_id},
            ),
        )

    def recurrent_events_cancels(self, events: list[tuple]):
        if events:
            return list(
                self.db.execute(
                    text("""
                select event_id, break_type, start, end from event_breaks
                where event_id in (:event_ids)
            """),
                    {"event_ids": ",".join(str(e[-1]) for e in events)},
                ),
            )
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
        day_start = datetime.combine(day, time(0, 0))
        day_end = datetime.combine(day, time(23, 59))
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

    def day_schedule(self, executor_id: int, day: date, user_id: int | None = None):
        events = self.events_for_day(executor_id, day) + self.recurrent_events_for_day(executor_id, day)
        events = sorted(events, key=lambda x: x[0])
        if user_id is not None:
            events = filter(lambda x: x[2] == user_id, events)
        return events

    def available_weekdays(self, executor_id: int):
        start_of_week = datetime.now().date() - timedelta(days=datetime.now().weekday())
        result = []
        for i in range(7):
            current_day = start_of_week + timedelta(days=i)
            events = self.recurrent_events_for_day(executor_id, current_day)
            start = datetime.combine(current_day, time(0, 0))
            end = datetime.combine(current_day, time(23, 59))
            available_time = self._get_available_slots(start, end, timedelta(hours=1), events)
            if available_time:
                result.append(i)
        return result

    def available_time(self, executor_id: int, day: date):
        events = self.events_for_day(executor_id, day)
        # TODO start and end with consideration for work hours
        start = datetime.combine(day, time(0, 0))
        end = datetime.combine(day, time(23, 59))
        # TODO slots by 15 minutes
        return [s[0] for s in self._get_available_slots(start, end, timedelta(hours=1), events)]

    def available_time_weekday(self, executor_id: int, weekday: int):
        start_of_week = datetime.now().date() - timedelta(days=datetime.now().weekday())
        current_day = start_of_week + timedelta(days=weekday)
        events = self.recurrent_events_for_day(executor_id, current_day)
        start = datetime.combine(current_day, time(0, 0))
        end = datetime.combine(current_day, time(23, 59))
        return [s[0] for s in self._get_available_slots(start, end, timedelta(hours=1), events)]

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
                occupied_start = datetime.strptime(occupied[0], DB_DATETIME) if isinstance(occupied[0], str) else occupied[0]
                occupied_end = datetime.strptime(occupied[1], DB_DATETIME) if isinstance(occupied[1], str) else occupied[1]
                if not (slot_end <= occupied_start or slot_start >= occupied_end):
                    return True  # The slot is occupied
            return False  # The slot is available

        # Filter out occupied slots
        return [slot for slot in all_slots if not is_occupied(slot)]

    def all_user_lessons(self, user: User):
        recurs = self._recurrent_events_executor(user.executor_id)
        events = self._events_executor(user.executor_id)
        result = []
        for e in recurs + events:
            if e.event_type not in self.LESSON_TYPES or e.user_id != user.id:
                continue
            result.append(e)
        return result

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
            event = (
                self.db.query(RecurrentEvent)
                .filter(
                    RecurrentEvent.executor_id == executor_id,
                    RecurrentEvent.event_type == RecurrentEvent.EventTypes.WORK_END,
                )
                .first()
            )
            event_time = event.start.time()
        elif kind == "start":
            event = self.db.query(RecurrentEvent).filter(
                RecurrentEvent.executor_id == executor_id,
                RecurrentEvent.event_type == RecurrentEvent.EventTypes.WORK_START,
            ).first()
            event_time = event.end.time()
        else:
            raise Exception("message", "Неизвестный тип события", f"unknown kind: {kind}")
        self.db.delete(event)
        self.db.commit()
        return event_time

    def weekends(self, executor_id: int):
        events = self._recurrent_events_executor(executor_id)
        weekends = filter(
            lambda x: x.event_type == RecurrentEvent.EventTypes.WEEKEND,
            events,
        )
        return list(weekends)

    def available_work_weekdays(self, executor_id: int):
        weekends = [w.start.weekday() for w in self.weekends(executor_id)]
        return [i for i in range(7) if i not in weekends]

    def vacations(self, user_id: int):
        events = self.db.execute(
            text("""
                select start, end, id from events
                where user_id = :user_id and event_type = :vacation and cancelled is false
            """),
            {"user_id": user_id, "vacation": Event.EventTypes.VACATION}
        )
        return list(events)
