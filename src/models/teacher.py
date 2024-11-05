from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models import Base
from models.user import User

if TYPE_CHECKING:
    from models import Student, Vacations, Weekend, WorkBreak


class Teacher(Base):
    """Teacher model."""

    __tablename__ = "teacher"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    user: Mapped[User] = relationship(back_populates="teacher")
    students: Mapped[list[Student]] = relationship(back_populates="teacher")
    breaks: Mapped[list[WorkBreak]] = relationship(back_populates="teacher")
    weekends: Mapped[list[Weekend]] = relationship(back_populates="teacher")
    holidays: Mapped[list[Vacations]] = relationship(back_populates="teacher")
