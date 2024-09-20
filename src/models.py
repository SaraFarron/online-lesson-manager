from __future__ import annotations

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
    work_start: Mapped[Time] = mapped_column(Time, default=config.WORK_START)
    work_end: Mapped[Time] = mapped_column(Time, default=config.WORK_END)
    weekends: Mapped[list[Weekend]] = relationship(back_populates="teacher")
    breaks: Mapped[list[WorkBreak]] = relationship(back_populates="teacher")
    students: Mapped[list[User]] = relationship(back_populates="teacher")


class Weekend(Base):
    __tablename__ = "weekend"

    id: Mapped[int] = mapped_column(primary_key=True)
    weekday: Mapped[int] = mapped_column(Integer)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="weekends")


class WorkBreak(Base):
    __tablename__ = "work_break"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="breaks")
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[Time] = mapped_column(Time)
    end_time: Mapped[Time] = mapped_column(Time)


class User(Base):
    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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


class Lesson(Base):
    __tablename__ = "lesson"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="lessons")
    date: Mapped[Date] = mapped_column(Date)
    time: Mapped[Time] = mapped_column(Time)
    end_time: Mapped[Time] = mapped_column(Time)
    # statuses: upcoming, canceled, completed
    status: Mapped[str] = mapped_column(String(10), default="upcoming")

    def __repr__(self) -> str:
        """String model represetation."""
        return f"Lesson(id={self.id!r}, date={self.date!r}, time={self.time!r}, user_id={self.user_id!r})"


class ScheduledLesson(Base):
    __tablename__ = "scheduled_lesson"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="scheduled_lessons")
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[Time] = mapped_column(Time)
    end_time: Mapped[Time] = mapped_column(Time)

    def __repr__(self) -> str:
        """String model represetation."""
        return f"Lesson(id={self.id!r}, weekday={self.weekday!r}, time={self.start_time!r}, user_id={self.user_id!r})"


class Reschedule(Base):
    __tablename__ = "reschedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="reschedules")
    source_id: Mapped[int] = mapped_column(ForeignKey("scheduled_lesson.id"))
    source: Mapped[ScheduledLesson] = relationship()
    source_date: Mapped[Date] = mapped_column(Date)
    date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True, default=None)  # noqa: UP007
    start_time: Mapped[Optional[Time]] = mapped_column(Time, nullable=True, default=None)  # noqa: UP007
    end_time: Mapped[Optional[Time]] = mapped_column(Time, nullable=True, default=None)  # noqa: UP007


class RestrictedTime(Base):
    __tablename__ = "restricted_time"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="restricted_times")
    weekday: Mapped[int] = mapped_column(Integer)
    whole_day_restricted: Mapped[bool] = mapped_column(default=False)
    start_time: Mapped[Optional[Time]] = mapped_column(Time, nullable=True, default=None)  # noqa: UP007
    end_time: Mapped[Optional[Time]] = mapped_column(Time, nullable=True, default=None)  # noqa: UP007
