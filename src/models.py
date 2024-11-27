from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import Date, ForeignKey, Integer, String, Time
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import config


class Base(DeclarativeBase):
    pass


class Teacher(Base):
    __tablename__ = "teacher"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    telegram_id: Mapped[int] = mapped_column(unique=True, nullable=False)
    work_start: Mapped[time] = mapped_column(Time, default=config.WORK_START)
    work_end: Mapped[time] = mapped_column(Time, default=config.WORK_END)
    weekends: Mapped[list[Weekend]] = relationship(back_populates="teacher")
    breaks: Mapped[list[WorkBreak]] = relationship(back_populates="teacher")
    holidays: Mapped[list[Vacations]] = relationship(back_populates="teacher")
    students: Mapped[list[User]] = relationship(back_populates="teacher")


class Vacations(Base):
    __tablename__ = "holidays"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="holidays")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)


class WeekdayMixin:
    weekday: Mapped[int] = mapped_column(Integer)

    @property
    def weekday_full_str(self) -> str:
        """Weekday as a string."""
        return config.WEEKDAY_MAP_FULL[self.weekday]

    @property
    def weekday_short_str(self) -> str:
        """Weekday as a string."""
        return config.WEEKDAY_MAP[self.weekday]


class Weekend(WeekdayMixin, Base):
    __tablename__ = "weekend"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="weekends")


class BordersMixin:
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

    @property
    def st_str(self):
        """Start time as a string."""
        return self.start_time.strftime("%H:%M")

    @property
    def et_str(self):
        """End time as a string."""
        return self.end_time.strftime("%H:%M")

    @property
    def edges(self):
        """Start and end time as a tuple."""
        return (self.start_time, self.end_time)


class WorkBreak(WeekdayMixin, BordersMixin, Base):
    __tablename__ = "work_break"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="breaks")


class User(Base):
    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # noqa: UP007
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="students")
    lessons: Mapped[list[Lesson]] = relationship(back_populates="user", cascade="all, delete")
    scheduled_lessons: Mapped[list[ScheduledLesson]] = relationship(back_populates="user", cascade="all, delete")
    reschedules: Mapped[list[Reschedule]] = relationship(back_populates="user", cascade="all, delete")
    restricted_times: Mapped[list[RestrictedTime]] = relationship(back_populates="user")

    @property
    def username_dog(self) -> str:
        """Telegram username dog."""
        return f"@{self.telegram_username}"

    @property
    def username_link(self) -> str:
        """Telegram username link."""
        return f"https://t.me/{self.telegram_username}"

    def __repr__(self) -> str:
        """String model represetation."""
        return f"User(id={self.id!r}, name={self.name!r})"


class Lesson(BordersMixin, Base):
    __tablename__ = "lesson"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="lessons")
    date: Mapped[Date] = mapped_column(Date)
    # statuses: upcoming, canceled, completed
    status: Mapped[str] = mapped_column(String(10), default="upcoming")

    @property
    def short_repr(self) -> str:
        """String model represetation."""
        return f"Урок в {self.st_str}-{self.et_str}"

    def __repr__(self) -> str:
        """String model represetation."""
        return f"Lesson(id={self.id!r}, date={self.date!r}, time={self.start_time!r}, user_id={self.user_id!r})"


class ScheduledLesson(WeekdayMixin, BordersMixin, Base):
    __tablename__ = "scheduled_lesson"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="scheduled_lessons")

    @property
    def short_repr(self) -> str:
        """String model represetation."""
        return f"Урок в {self.st_str}-{self.et_str}"

    def may_cancel(self, date_time: datetime) -> bool:
        """Check if reschedule may be canceled."""
        if self.weekday != date_time.weekday():
            return True
        delta = datetime.combine(date_time.date(), self.start_time, tzinfo=config.TIMEZONE) - date_time
        return bool(delta > timedelta(0) and delta > timedelta(hours=config.HRS_TO_CANCEL))

    def __repr__(self) -> str:
        """String model represetation."""
        return f"Урок на {self.weekday_short_str} в {self.st_str}"


class Reschedule(BordersMixin, Base):
    __tablename__ = "reschedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="reschedules")
    source_id: Mapped[int] = mapped_column(ForeignKey("scheduled_lesson.id"))
    source: Mapped[ScheduledLesson] = relationship()
    source_date: Mapped[date] = mapped_column(Date)
    date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, default=None)  # noqa: UP007

    @property
    def short_repr(self) -> str:
        """String model represetation."""
        return f"Перенос в {self.st_str}-{self.et_str}"

    @property
    def weekday(self):
        """Weekday."""
        return self.date.weekday() if self.date else None

    def may_cancel(self, date_time: datetime) -> bool:
        """Check if reschedule may be canceled."""
        if not self.date:
            return False
        if self.date > date_time.date():
            return True
        delta = datetime.combine(self.date, self.start_time, tzinfo=config.TIMEZONE) - date_time
        return bool(delta > timedelta(0) and delta > timedelta(hours=config.HRS_TO_CANCEL))

    def __repr__(self) -> str:
        """String model represetation."""
        return f"Перенос на {self.date} в {self.st_str}"


class RestrictedTime(WeekdayMixin, BordersMixin, Base):
    __tablename__ = "restricted_time"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="restricted_times")
    whole_day_restricted: Mapped[bool] = mapped_column(default=False)
