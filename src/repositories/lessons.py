from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time

from sqlalchemy.orm import Session

from config.config import MAX_HOUR
from models import Lesson, Reschedule, ScheduledLesson, User
from repositories import Repository


def calc_end_time(time: time):
    """Calculate end time."""
    return time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0)


class ScheduledLessonRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize scheduled lesson repository class."""
        super().__init__(ScheduledLesson, session)

    def new(self, user: User, weekday: int, start_time: time) -> None:
        """Add new entry of model to the database."""
        sl = ScheduledLesson(user=user, weekday=weekday, start_time=start_time, end_time=calc_end_time(start_time))
        self.session.add(sl)

    def all(
        self,
        user: User | None = None,
        weekday: int | None = None,
        start_time: time | None = None,
    ):
        """Get all entries of model from the database."""
        query = self.session.query(ScheduledLesson)
        filters = {}
        if user:
            filters["user"] = user
        if weekday is not None:
            filters["weekday"] = weekday
        if start_time:
            filters["start_time"] = start_time

        return query.filter_by(**filters).all()


class RescheduleRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize reschedule repository class."""
        super().__init__(Reschedule, session)

    def new(
        self,
        user: User,
        sl: ScheduledLesson,
        source_date: date,
        new_date: date | None = None,
        new_time: time | None = None,
    ) -> Reschedule:
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
        return reschedule

    def get_many(self, whereclause, limit: int = 100, order_by=None) -> Sequence[Reschedule]:  # noqa: D102, ANN001
        return super().get_many(whereclause, limit, order_by)  # type: ignore  # noqa: PGH003

    def all(
        self,
        user: User | None = None,
        source: ScheduledLesson | None = None,
        date: date | None = None,
        start_time: time | None = None,
    ):
        """Get all reschedules from the database."""
        query = self.session.query(Reschedule)
        filters = {}
        if user:
            filters["user"] = user
        if source:
            filters["source"] = source
        if date is not None:
            filters["date"] = date
        if start_time:
            filters["start_time"] = start_time

        return query.filter_by(**filters).all()


class LessonRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize lesson repository class."""
        super().__init__(Lesson, session)

    def new(self, user: User, date: date, start_time: time) -> None:
        """Add new entry of model to the database."""
        lesson = Lesson(user=user, date=date, start_time=start_time, end_time=calc_end_time(start_time))
        self.session.add(lesson)

    def all(
        self,
        user: User | None = None,
        date: date | None = None,
        start_time: time | None = None,
    ):
        """Get all entries of model from the database."""
        query = self.session.query(Lesson)
        filters = {}
        if user:
            filters["user"] = user
        if date is not None:
            filters["date"] = date
        if start_time:
            filters["start_time"] = start_time

        return query.filter_by(**filters).all()


class LessonCollectionRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize lesson collection repository class."""
        self.session = session
        self.lessons = LessonRepo(self.session)
        self.scheduled_lessons = ScheduledLessonRepo(self.session)
        self.reschedules = RescheduleRepo(self.session)

    def new(self, lesson_type: Lesson | ScheduledLesson | Reschedule, *args, **kwargs) -> Reschedule |None:  # noqa: ANN002, ANN003
        """Add new entry of model to the database."""
        if type(lesson_type) is Lesson:
            repo = LessonRepo(self.session)
        elif type(lesson_type) is ScheduledLesson:
            repo = ScheduledLessonRepo(self.session)
        elif type(lesson_type) is Reschedule:
            repo = RescheduleRepo(self.session)
        else:
            msg = "Unknown lesson type"
            raise TypeError(msg)
        if not repo:
            raise TypeError
        return repo.new(*args, **kwargs)

    def all(
        self,
        user: User | None = None,
        date: date | None = None,
        weekday: int | None = None,
        start_time: time | None = None,
    ):
        """Get all entries of model from the database."""
        # TODO Finish this shit
        lessons = LessonRepo(self.session).all(user, date, start_time)
        scheduled_lessons = ScheduledLessonRepo(self.session).all(user, weekday, start_time)
        reschedules = RescheduleRepo(self.session).all(user, None, date, start_time)
        if date and scheduled_lessons:
            sl_ids = [sl.id for sl in scheduled_lessons]
            cancellations = self.session.query(Reschedule).filter(
                Reschedule.user == user, Reschedule.source_id.in_(sl_ids),
            )
            cancellation_ids = [c.source_id for c in cancellations]
            scheduled_lessons = [sl for sl in scheduled_lessons if sl.id not in cancellation_ids]
        return lessons + scheduled_lessons + reschedules

    def all_date(self, date: date, user: User | None = None):
        """Get all entries of model from the database."""
        lessons = self.lessons.all(user, date)
        scheduled_lessons = self.scheduled_lessons.all(user, date.weekday())
        reschedules = self.reschedules.all(user, None, date)
        return lessons + scheduled_lessons + reschedules
