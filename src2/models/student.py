from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src2.models import User

if TYPE_CHECKING:
    from src2.models import Reschedule, RestrictedTime, ScheduledLesson, Teacher


class Student(User):
    """Student model."""

    __tablename__ = "student"

    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="students")
    scheduled_lessons: Mapped[list[ScheduledLesson]] = relationship(back_populates="user", cascade="all, delete")
    reschedules: Mapped[list[Reschedule]] = relationship(back_populates="user", cascade="all, delete")
    restricted_times: Mapped[list[RestrictedTime]] = relationship(back_populates="user")

    @property
    def username_dog(self) -> str:
        """Telegram username dog."""
        return f"@{self.user_name}"

    @property
    def username_link(self) -> str:
        """Telegram username link."""
        return f"https://t.me/{self.user_name}"

    def __repr__(self) -> str:
        """String model represetation."""
        return f"User(id={self.id!r}, name={self.user_name!r})"
