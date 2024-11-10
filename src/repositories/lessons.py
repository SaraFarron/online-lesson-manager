from __future__ import annotations

from datetime import date, time

from config.config import MAX_HOUR
from models import Lesson, Reschedule, ScheduledLesson, User
from repositories import Repository


def calc_end_time(time: time):
    """Calculate end time."""
    return time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0)


class ScheduledLessonRepo(Repository):
    def new(self, user: User, weekday: int, start_time: time) -> None:
        """Add new entry of model to the database."""
        sl = ScheduledLesson(user=user, weekday=weekday, start_time=start_time, end_time=calc_end_time(start_time))
        self.session.add(sl)


class RescheduleRepo(Repository):
    def new(
        self,
        user: User,
        sl: ScheduledLesson,
        source_date: date,
        new_date: date | None = None,
        new_time: time | None = None,
    ) -> None:
        """Add new entry of model to the database."""
        reschedule = Reschedule(
            user=user,
            source=sl,
            source_date=source_date,
            date=new_date,
            start_time=new_time,
            end_time=calc_end_time(new_time) if new_time else None,
        )
        self.session.add(reschedule)


class LessonRepo(Repository):
    def new(self, user: User, date: date, start_time: time) -> None:
        """Add new entry of model to the database."""
        lesson = Lesson(user=user, date=date, start_time=start_time, end_time=calc_end_time(start_time))
        self.session.add(lesson)


class LessonCollectionRepo(Repository):
    pass
