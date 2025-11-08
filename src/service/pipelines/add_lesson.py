from abs import Pipeline


class AddLessonPipeline(Pipeline):
    def new_lesson_data_is_fine(self):
        """
        Check if new lesson data:
        - not conflicting self.date_time_is_fine
        - not conflicting MAX_LESSONS_PER_DAY setting
        """
    
    def choose_lesson_date(self, day: str):
        # day_is_fine
        pass
    
    def choose_lesson_time(self, time: str):
        # new_lesson_data_is_fine
        pass


class AddRecurrentLessonPipeline(Pipeline):
    def new_recurrent_lesson_data_is_fine(self):
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
