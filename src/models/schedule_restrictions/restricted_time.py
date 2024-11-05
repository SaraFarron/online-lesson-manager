from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models import Base

if TYPE_CHECKING:
    from models import User
from models.mixins import BordersMixin, WeekdayMixin


class RestrictedTime(WeekdayMixin, BordersMixin, Base):
    __tablename__ = "restricted_time"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("student.id"))
    user: Mapped[User] = relationship(back_populates="restricted_times")
    whole_day_restricted: Mapped[bool] = mapped_column(default=False)
