from datetime import date, time

from models import RestrictedTime, Teacher, User, Vacations, Weekend, WorkBreak
from repositories import Repository


class WeekendRepo(Repository):
    def new(self, teacher: Teacher, weekday: int) -> None:
        """Add new entry of model to the database."""
        weekend = Weekend(
            teacher_id=teacher.id,
            teacher=teacher,
            weekday=weekday,
        )
        self.session.add(weekend)


class VacationsRepo(Repository):
    def new(self, teacher: Teacher, start_date: date, end_date: date) -> None:
        """Add new entry of model to the database."""
        vacation = Vacations(
            teacher_id=teacher.id,
            teacher=teacher,
            start_date=start_date,
            end_date=end_date,
        )
        self.session.add(vacation)


class WorkBreakRepo(Repository):
    def new(self, teacher: Teacher, weekday: int, start_time: time, end_time: time) -> None:
        """Add new entry of model to the database."""
        work_break = WorkBreak(
            teacher_id=teacher.id,
            teacher=teacher,
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
        )
        self.session.add(work_break)


class RestrictedTimeRepo(Repository):
    def new(self, user: User, weekday: int, start_time: time, end_time: time, whole_day: bool = False) -> None:  # noqa: FBT002, FBT001
        """Add new entry of model to the database."""
        restricted_time = RestrictedTime(
            user_id=user.id,
            user=user,
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
            whole_day_restricted=whole_day,
        )
        self.session.add(restricted_time)


class RestrictionsRepo(Repository):
    pass
