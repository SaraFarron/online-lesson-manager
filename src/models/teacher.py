from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from models import User

if TYPE_CHECKING:
    from models import Student, Vacations, Weekend, WorkBreak


class Teacher(User):
    """Teacher model."""

    __tablename__ = "teacher"

    students: Mapped[list[Student]] = relationship(back_populates="teacher")
    breaks: Mapped[list[WorkBreak]] = relationship(back_populates="teacher")
    weekends: Mapped[list[Weekend]] = relationship(back_populates="teacher")
    holidays: Mapped[list[Vacations]] = relationship(back_populates="teacher")
