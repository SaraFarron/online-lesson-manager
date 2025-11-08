from abs import Pipeline


class MoveDeleteLessonPipeline(Pipeline):
    def type_date(self):
        pass

    def choose_time(self):
        pass


class MoveDeleteRecurrentLessonPipeline(Pipeline):
    def once_or_forever(self):
        pass
    
    # ---- RECURRENT LESSON MOVE FOREVER ---- #
    
    def choose_weekday(self):
        pass
    
    def choose_recur_time(self):
        pass
    
    # ---- RECURRENT LESSON ACTION ONCE ---- #
    
    def type_recur_date(self):
        pass
    
    # ---- RECURRENT LESSON MOVE ONCE ---- #
    
    def type_recur_new_date(self):
        pass
    
    def choose_recur_new_time(self):
        pass


class MoveDeletePipeline(MoveDeleteLessonPipeline, MoveDeleteRecurrentLessonPipeline):
    def choose_lesson(self):
        pass
    
    def move_or_delete(self):
        pass
