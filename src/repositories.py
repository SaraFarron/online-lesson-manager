from datetime import date, datetime, timedelta, time

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models import EventHistory, Executor, User


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


class EventRepo(Repo):
    def events_for_day(self, executor_id: int, day: date):
        events = self.db.execute(text("""
            select start_time, end_time, user_id, event_type, is_reschedule from events
            where executor_id = :executor_id and date = :day and cancelled is false
            order by start_time desc
        """), {"executor_id": executor_id, "day": day})
        return list(events)

    def available_time(self, executor_id: int, day: date):
        events = self.events_for_day(executor_id, day)
        start = datetime.combine(day, time(0, 0))
        end = datetime.combine(day, time(23, 59))
        return self._get_available_slots(start, end, timedelta(hours=1), events)

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
                occupied_start, occupied_end = occupied[0], occupied[1]
                if not (slot_end <= occupied_start or slot_start >= occupied_end):
                    return True  # The slot is occupied
            return False  # The slot is available

        # Filter out occupied slots
        return [slot for slot in all_slots if not is_occupied(slot)]
