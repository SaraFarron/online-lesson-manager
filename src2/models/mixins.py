from datetime import time

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Integer, Time

from config import config


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
