from sqlalchemy import Column, Integer, String, ForeignKey, Time, Boolean, Date, DateTime
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timedelta

Base = declarative_base()

TIME_FMT = "%H:%M"


class Model:
    id = Column(Integer, primary_key=True, autoincrement=True)


class Executor(Model, Base):
    __tablename__ = 'executors'
    user = relationship('User', backref='executor')
    jobs = relationship('Event', back_populates='executor')
    recurrent_jobs = relationship('RecurrentEvent', back_populates='executor')


class User(Model, Base):
    __tablename__ = 'users'
    name = Column(String)
    role = Column(String)
    events = relationship('Event', back_populates='user')
    recurrent_events = relationship('RecurrentEvent', back_populates='user')
    event_breaks = relationship('EventBreak', back_populates='user')
    executor_id = Column(Integer, ForeignKey('executors.id'), nullable=True, default=None)


class Event(Model, Base):
    __tablename__ = 'events'
    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship(User, back_populates='events')
    executor_id = Column(Integer, ForeignKey('executors.id'), nullable=False)
    executor = relationship(Executor, back_populates='jobs')
    event_type = Column(String)
    start_time = Column(Time)
    end_time = Column(Time)
    date = Column(Date)
    weekday = Column(Integer)
    cancelled = Column(Boolean, default=False)
    reschedule_id = Column(Integer, ForeignKey('events.id'), nullable=True, default=None)
    reschedule = relationship('Event')
    is_reschedule = Column(Boolean, default=False)

    @property
    def st_str(self):
        """Start time as a string."""
        return self.start_time.strftime(TIME_FMT)

    @property
    def et_str(self):
        """End time as a string."""
        return self.end_time.strftime(TIME_FMT)

    def __str__(self):
        if self.is_reschedule:
            return f"Перенос в {self.st_str}-{self.et_str}"
        return f"Урок в {self.st_str}-{self.et_str}"

class RecurrentEvent(Model, Base):
    __tablename__ = 'recurrent_events'
    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship(User, back_populates='recurrent_events')
    executor_id = Column(Integer, ForeignKey('executors.id'), nullable=False)
    executor = relationship(Executor, back_populates='recurrent_jobs')
    event_id = Column(Integer, ForeignKey('events.id'))
    event = relationship(Event)
    interval = Column(Integer)
    start = Column(DateTime)
    end = Column(DateTime, nullable=True)

    @property
    def st_str(self):
        """Start time as a string."""
        return self.start_time.strftime(TIME_FMT)

    @property
    def et_str(self):
        """End time as a string."""
        return self.end_time.strftime(TIME_FMT)

    def __str__(self):
        return f"Урок в {self.st_str}-{self.et_str}"

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


class EventBreak(Model, Base):
    __tablename__ = 'event_breaks'
    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship(User, back_populates='event_breaks')
    break_type = Column(String)
    start = Column(DateTime)
    end = Column(DateTime)


class EventHistory(Model, Base):
    __tablename__ = 'event_history'
    author = Column(String)
    scene = Column(String)
    event_type = Column(String)
    event_value = Column(String)
    created_at = Column(DateTime, default=datetime.now)
