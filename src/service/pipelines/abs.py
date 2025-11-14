from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as _time

from sqlalchemy.orm import Session

from src.interface.messages import replies


@dataclass
class Pipeline:
    db: Session
    user_id: int
    telegram_id: int
    executor_id: int | None = None
    input_date: date | None = None
    input_time: _time | None = None
    input_weekday: int | None = None  # 0 - Monday, 6 - Sunday
    
    def _day_is_fine(self, day: date):
        """Check if day not in past."""
        now_date = datetime.now().date()
        assert now_date <= day, replies.CHOOSE_FUTURE_DATE
    
    def _slot_is_available(self, slot: datetime):
        """Check if time slot is available on given day."""
    
    def _date_time_is_fine(self):
        """
        Check if date and time:
        - not in past
        - not conflicting with HRS_TO_CANCEL setting
        - not conflicting self.slot_is_available
        """
    
    def _week_slot_is_available(self, slot: datetime):
        """Check if time slot is available on given weekday."""
    
    def _weekday_is_fine(self, weekday: int):
        """Check if weekday has available time slots."""
    
    def _weekday_time_is_fine(self, weekday: int, time: _time):
        """
        Check if weekday and time:
        - not conflicting with HRS_TO_CANCEL setting (WARNING)
        - not conflicting self.week_slot_is_available
        """
