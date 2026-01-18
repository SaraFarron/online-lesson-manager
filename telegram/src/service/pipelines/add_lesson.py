from dataclasses import dataclass
from datetime import time as _time

from db.getters import get_exec_work_hours_by_user_id
from db.makers import create_lesson
from interface.messages import replies
from service.lessons import available_time_for_day
from service.pipelines.abs import Pipeline
from service.utils import parse_date


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
        
        executor = get_exec_work_hours_by_user_id(self.db, self.user_id)
        self.executor_id = executor.id
        return available_time_for_day(
            self.db, self.user_id, date, executor.id,
        )

    def choose_lesson_time(self, time: _time):
        assert self.executor_id is not None
        assert self.input_date is not None

        available_time = available_time_for_day(
            self.db, self.user_id, self.input_date,
        )
        assert time.strftime("%H:%M") in available_time, replies.TIME_NOT_AVAILABLE
        create_lesson(
            self.db, self.user_id, self.executor_id, self.input_date, time,
        )
        return replies.LESSON_ADDED


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
