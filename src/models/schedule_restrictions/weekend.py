from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models import Base
from models.mixins import WeekdayMixin

if TYPE_CHECKING:
    from models import Teacher


class Weekend(WeekdayMixin, Base):
    __tablename__ = "weekend"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="weekends")
