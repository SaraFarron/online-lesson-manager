from abs import Pipeline
from service.utils import parse_date, parse_time
from service.lessons import available_time_for_day
from datetime import time as _time
from dataclasses import dataclass
from interface.messages import replies


@dataclass
class AddLessonPipeline(Pipeline):
    
    def _new_lesson_data_is_fine(self):
        """
        Check if new lesson data:
        - not conflicting self.date_time_is_fine
        - not conflicting MAX_LESSONS_PER_DAY setting
        """
    
    def choose_lesson_date(self, day: str):
        date = parse_date(day)
        assert date, replies.WRONG_DATE_FMT
        self._day_is_fine(date)
        self.input_date = date
        available_time = available_time_for_day(self.db, self.user_id, date)
        assert available_time, replies.NO_TIME
        return available_time
        
    
    def choose_lesson_time(self, time: _time):
        pass


class AddRecurrentLessonPipeline(Pipeline):
    def _new_recurrent_lesson_data_is_fine(self):
        """
        Check if new recurrent lesson data:
        - not conflicting self.weekday_time_is_fine
        - not conflicting MAX_LESSONS_PER_DAY setting
        - there is no existing recurrent lesson at given weekday and time
        """
    
    def choose_weekday(self, weekday: int):
        # weekday_is_fine
        pass
    
    def choose_time(self, time: str):
        # new_recurrent_lesson_data_is_fine
        pass
