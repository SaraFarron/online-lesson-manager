from datetime import datetime, timedelta

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import declarative_base, relationship

from src.core.config import DATE_FMT, DATETIME_FMT, SHORT_DATE_FMT, TIME_FMT, WEEKDAY_MAP

Base = declarative_base()


class Model:
    @declared_attr
    def id(self):
        return Column(Integer, primary_key=True, autoincrement=True)


class Executor(Model, Base):
    __tablename__ = "executors"
    code = Column(String, unique=True)
    user = relationship("User", backref="executor")
    telegram_id = Column(Integer, unique=True)


class User(Model, Base):
    __tablename__ = "users"
    telegram_id = Column(Integer, unique=True)
    username = Column(String)
    full_name = Column(String)
    role = Column(String)
    executor_id = Column(Integer, ForeignKey("executors.id"), nullable=True, default=None)

    class Roles:
        TEACHER = "TEACHER"
        STUDENT = "STUDENT"


class EventModel(Model):
    @declared_attr
    def user_id(self):
        return Column(Integer, ForeignKey("users.id"))

    @declared_attr
    def user(self):
        return relationship(User)

    @declared_attr
    def executor_id(self):
        return Column(Integer, ForeignKey("executors.id"), nullable=False)

    @declared_attr
    def executor(self):
        return relationship(Executor)

    @declared_attr
    def event_type(self):
        return Column(String)

    @declared_attr
    def start(self):
        return Column(DateTime)

    @declared_attr
    def end(self):
        return Column(DateTime)


class Event(EventModel, Base):
    __tablename__ = "events"
    cancelled = Column(Boolean, default=False)
    reschedule_id = Column(Integer, ForeignKey("events.id"), nullable=True, default=None)
    reschedule = relationship("Event")
    is_reschedule = Column(Boolean, default=False)

    @property
    def st_str(self):
        """Start time as a string."""
        return self.start.strftime(DATETIME_FMT)

    @property
    def et_str(self):
        """End time as a string."""
        return self.end.strftime(DATETIME_FMT)

    class EventTypes:
        LESSON = "Урок"
        MOVED_LESSON = "Перенос"
        VACATION = "Каникулы"

    def __str__(self) -> str:
        match self.event_type:
            case self.EventTypes.LESSON:
                date = datetime.strftime(self.start, DATE_FMT)
                time = datetime.strftime(self.start, TIME_FMT)
                return f"Урок {date} в {time}"
            case self.EventTypes.MOVED_LESSON:
                date = datetime.strftime(self.start, DATE_FMT)
                time = datetime.strftime(self.start, TIME_FMT)
                return f"Перенос {date} в {time}"
            case self.EventTypes.VACATION:
                start, end = datetime.strftime(self.start, DATE_FMT), datetime.strftime(self.end, DATE_FMT)
                return f"Каникулы с {start} по {end}"
            case _:
                return f"{self.event_type} {self.st_str}-{self.et_str}"


class RecurrentEvent(EventModel, Base):
    __tablename__ = "recurrent_events"
    interval = Column(Integer)  # days
    interval_end = Column(DateTime, nullable=True, default=None)

    def get_next_occurrence(self, after: datetime, before: datetime | None = None):
        """
        Given an Event and a datetime, return the next occurrence of the event after the given datetime.
        """
        if after < self.start:
            return None

        elapsed_time = (after - self.start).total_seconds()
        intervals_passed = (elapsed_time // self.interval) + 1
        occur = self.start + timedelta(seconds=intervals_passed * self.interval)
        if before and occur > before:
            return None
        return occur

    class EventTypes:
        LESSON = "Урок"
        WORK_START = "Начало рабочего дня"
        WORK_END = "Конец рабочего дня"
        WEEKEND = "Выходной"
        WORK_BREAK = "Перерыв"

    def __str__(self) -> str:
        match self.event_type:
            case self.EventTypes.LESSON:
                weekday = WEEKDAY_MAP[self.start.weekday()]["long"]
                time = datetime.strftime(self.start, TIME_FMT)
                return f"Урок в {weekday} {time}"
            case self.EventTypes.WORK_START:
                time = datetime.strftime(self.end, TIME_FMT)
                return f"{self.event_type} в {time}"
            case self.EventTypes.WORK_START:
                time = datetime.strftime(self.start, TIME_FMT)
                return f"{self.event_type} в {time}"
            case _:
                return f"{self.event_type} {self.start} {self.interval}"


class CancelledRecurrentEvent(Model, Base):
    __tablename__ = "event_breaks"
    event_id = Column(Integer, ForeignKey("recurrent_events.id"))
    event = relationship(RecurrentEvent)
    break_type = Column(String)
    start = Column(DateTime)
    end = Column(DateTime)

    class CancelTypes:
        LESSON_CANCELED = "Отмена занятия"


class EventHistory(Model, Base):
    __tablename__ = "event_history"
    author = Column(String)
    scene = Column(String)
    event_type = Column(String)
    event_value = Column(String)
    created_at = Column(DateTime, default=datetime.now)


class HomeWork:
    user_id = 0
    user = None
    executor_id = 0
    executor = None
    created_at = datetime.now()
    filename = "test"
    status = "DONE"
    # TODO Replace with Model in future


# class HomeWork(Model, Base):
#     @declared_attr
#     def user_id(self):
#         return Column(Integer, ForeignKey("users.id"))

#     @declared_attr
#     def user(self):
#         return relationship(User)

#     @declared_attr
#     def executor_id(self):
#         return Column(Integer, ForeignKey("executors.id"), nullable=False)

#     @declared_attr
#     def executor(self):
#         return relationship(Executor)

#     created_at = Column(DateTime, default=datetime.now)
#     filename = Column(String)
#     status = Column(String)

#     class HWStatus:
#         ONGOING = "Выдана"
#         DONE = "Выполнена"

#     def __str__(self) -> str:
#         return f"ДЗ от {datetime.strftime(self.created_at, SHORT_DATE_FMT)}"
