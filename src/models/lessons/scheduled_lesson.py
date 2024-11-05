from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config import settings
from models import Base
from models.mixins import BordersMixin, WeekdayMixin

if TYPE_CHECKING:
    from models import User


class ScheduledLesson(WeekdayMixin, BordersMixin, Base):
    __tablename__ = "scheduled_lesson"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("student.id"))
    user: Mapped[User] = relationship(back_populates="scheduled_lessons")

    @property
    def short_repr(self) -> str:
        """String model represetation."""
        return f"Урок в {self.st_str}-{self.et_str}"

    def may_cancel(self, date_time: datetime) -> bool:
        """Check if reschedule may be canceled."""
        if self.weekday != date_time.weekday():
            return True
        delta = datetime.combine(date_time.date(), self.start_time, tzinfo=settings.TIMEZONE) - date_time
        return bool(delta > timedelta(0) and delta > timedelta(hours=settings.HRS_TO_CANCEL))

    def __repr__(self) -> str:
        """String model represetation."""
        return f"Урок на {self.weekday_short_str} в {self.st_str}"
