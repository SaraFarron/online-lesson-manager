from __future__ import annotations

from datetime import date, time
from typing import Sequence

from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam

from models import RestrictedTime, Teacher, User, Vacations, Weekend, WorkBreak
from repositories import Repository


class WeekendRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize weekend repository class."""
        super().__init__(Weekend, session)

    def new(self, teacher: Teacher, weekday: int) -> None:
        """Add new entry of model to the database."""
        weekend = Weekend(
            teacher_id=teacher.id,
            teacher=teacher,
            weekday=weekday,
        )
        self.session.add(weekend)

    def all(self, teacher: Teacher | None = None, weekday: int | None = None, start_time: time | None = None):
        """Get all entries of model from the database."""
        query = self.session.query(Weekend)
        filters = {}
        if teacher:
            filters["teacher"] = teacher
        if weekday is not None:
            filters["weekday"] = weekday
        if start_time:
            filters["start_time"] = start_time
        return query.filter_by(**filters).all()


class VacationsRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize vacations repository class."""
        super().__init__(Vacations, session)

    def has_active_vacations(self, user: User, day: date, teacher: Teacher | None = None):
        if teacher is None:
            query = self.session.execute(text("""
                select count(*) as active_holiday from holidays
                where start_date <= :start_date and end_date >= :end_date and user_id = :user_id
                ;"""),
        {"start_date": day, "end_date": day, "user_id": user.id}
            )
        else:
            users = [s.id for s in teacher.students]
            print(users)
            query = self.session.execute(text("""
                select count(*) as active_holiday from holidays
                where start_date <= :start_date and end_date >= :end_date and user_id in :users
                ;""").bindparams(bindparam("users", expanding=True)),
        {"start_date": day, "end_date": day, "users": users}
            )
        return bool(query.fetchone()[0])

    def new(self, user: User, start_date: date, end_date: date) -> Vacations:
        """Add new entry of model to the database."""
        vacation = Vacations(
            user_id=user.id,
            user=user,
            start_date=start_date,
            end_date=end_date,
        )
        self.session.add(vacation)
        return vacation

    def all(self, user: User | None = None, start_date: date | None = None, end_date: date | None = None):
        """Get all entries of model from the database."""
        query = self.session.query(Vacations)
        filters = {}
        if user:
            filters["user"] = user
        if start_date:
            filters["start_date"] = start_date
        if end_date:
            filters["end_date"] = end_date
        return query.filter_by(**filters).all()


class WorkBreakRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize work breaks repository class."""
        super().__init__(WorkBreak, session)

    def get_many(self, whereclause, limit: int = 100, order_by=None) -> Sequence[WorkBreak]:
        """Get all entries of model from the database."""
        return super().get_many(whereclause, limit, order_by)

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

    def all(self, teacher: Teacher | None = None, weekday: int | None = None, start_time: time | None = None):
        """Get all entries of model from the database."""
        query = self.session.query(WorkBreak)
        filters = {}
        if teacher:
            filters["teacher"] = teacher
        if weekday is not None:
            filters["weekday"] = weekday
        if start_time:
            filters["start_time"] = start_time
        return query.filter_by(**filters).all()


class RestrictedTimeRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize restricted time repository class."""
        super().__init__(RestrictedTime, session)

    def new(self, user: User, weekday: int, start_time: time, end_time: time,
            whole_day: bool = False) -> None:  # noqa: FBT002, FBT001
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


class TeacherRestTimeRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize teacher rest time repository class."""
        self.session = session

    def new(self, restrict_type: Weekend | Vacations | WorkBreak, *args, **kwargs):  # noqa: ANN002, ANN003
        """Add new entry of model to the database."""
        if type(restrict_type) is Weekend:
            repo = WeekendRepo(self.session)
        elif type(restrict_type) is Vacations:
            repo = VacationsRepo(self.session)
        elif type(restrict_type) is WorkBreak:
            repo = WorkBreakRepo(self.session)
        else:
            msg = "Unknown restriction type"
            raise TypeError(msg)
        return repo.new(*args, **kwargs)

    def all(
            self,
            teacher: Teacher | None = None,
            weekday: int | None = None,
            date: date | None = None,
            time: time | None = None,
    ):
        """Get all entries of model from the database."""
        work_breaks = WorkBreakRepo(self.session).all(teacher, weekday, time)
        vacations = VacationsRepo(self.session).all(teacher, date, date)
        weekends = WeekendRepo(self.session).all(teacher, weekday, time)
        return work_breaks + vacations + weekends
