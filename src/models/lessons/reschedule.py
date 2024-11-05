from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Date

from config import settings
from models import Base, ScheduledLesson, User
from models.mixins import BordersMixin


class Reschedule(BordersMixin, Base):
    __tablename__ = "reschedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="reschedules")
    source_id: Mapped[int] = mapped_column(ForeignKey("scheduled_lesson.id"))
    source: Mapped[ScheduledLesson] = relationship()
    source_date: Mapped[date] = mapped_column(Date)
    date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)

    @property
    def short_repr(self) -> str:
        """String model represetation."""
        return f"Перенос в {self.st_str}-{self.et_str}"

    def may_cancel(self, date_time: datetime) -> bool:
        """Check if reschedule may be canceled."""
        if self.date is None:
            return False
        if self.date > date_time.date():
            return True
        delta = datetime.combine(self.date, self.start_time, tzinfo=settings.TIMEZONE) - date_time
        return bool(delta > timedelta(0) and delta > timedelta(hours=settings.HRS_TO_CANCEL))

    def __repr__(self) -> str:
        """String model represetation."""
        return f"Перенос на {self.date} в {self.st_str}"
