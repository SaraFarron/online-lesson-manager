from datetime import date, datetime
from datetime import time as _time


class Pipeline:
    def day_is_fine(self, day: date):
        """Check if day not in past."""
    
    def slot_is_available(self, slot: datetime):
        """Check if time slot is available on given day."""
    
    def date_time_is_fine(self):
        """
        Check if date and time:
        - not in past
        - not conflicting with HRS_TO_CANCEL setting
        - not conflicting self.slot_is_available
        """
    
    def week_slot_is_available(self, slot: datetime):
        """Check if time slot is available on given weekday."""
    
    def weekday_is_fine(self, weekday: int):
        """Check if weekday has available time slots."""
    
    def weekday_time_is_fine(self, weekday: int, time: _time):
        """
        Check if weekday and time:
        - not conflicting with HRS_TO_CANCEL setting (WARNING)
        - not conflicting self.week_slot_is_available
        """
