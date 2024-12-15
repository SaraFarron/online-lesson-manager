from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from itertools import chain

from aiogram import html
from sqlalchemy.orm import Session

from config.config import TIMEZONE
from models import Lesson, Reschedule, RestrictedTime, ScheduledLesson, Teacher, User, Vacations, WorkBreak
from repositories import LessonCollectionRepo, TeacherRepo, WeekendRepo, WorkBreakRepo

MAX_HOUR = 23


class Collisions:
    def __init__(self) -> None:
        self.weekends: list[ScheduledLesson | Reschedule] = []
        self.work_breaks: list[ScheduledLesson | Reschedule] = []
        self.work_borders: list[ScheduledLesson | Reschedule] = []

    @property
    def all_collisions(self) -> list[ScheduledLesson | Reschedule]:
        """Return all collisions."""
        return self.weekends + self.work_breaks + self.work_borders

    @property
    def message(self) -> str:
        """Return message with collisions."""
        message = ""
        for lesson in self.weekends:
            message += f"{lesson!s} у {lesson.user.username_dog} стоит в выходной\n"
        for lesson in self.work_breaks:
            message += f"{lesson!s} у {lesson.user.username_dog} стоит в перерыве\n"
        for lesson in self.work_borders:
            message += f"{lesson!s} у {lesson.user.username_dog} стоит в нерабочее время\n"
        if message:
            return "Уроки, пересекающиеся с выходными или рабочими перерывами:\n" + message
        return "Нет пересекающихся уроков"


class SessionBase:
    def __init__(self, session: Session) -> None:
        """Initialize schedule class."""
        self.session = session


class UserBase(SessionBase):
    def __init__(self, session: Session, user: User | int | None = None) -> None:
        """Initialize schedule class."""
        super().__init__(session)
        if isinstance(user, int):
            user = session.query(User).filter(User.telegram_id == user).first()
        self.user = user


class FirstFunctions(UserBase):
    def get_cancellations_day(self, day: datetime):
        """Get cancellations from the database."""
        if self.user is None:
            return self.session.query(Reschedule).filter(Reschedule.source_date == day.date()).all()
        return (
            self.session.query(Reschedule)
            .filter(Reschedule.source_date == day.date(), Reschedule.user_id == self.user.id)
            .all()
        )

    def get_events_day(self, day: datetime):
        """Get events from the database."""
        sl_query = self.session.query(ScheduledLesson)
        rs_query = self.session.query(Reschedule)
        cancellations = [x.source_id for x in self.get_cancellations_day(day)]

        if self.user:
            sl_query = sl_query.filter(ScheduledLesson.user_id == self.user.id)
            rs_query = rs_query.filter(Reschedule.user_id == self.user.id)

        sl_query = sl_query.filter(ScheduledLesson.weekday == day.weekday(), ScheduledLesson.id.not_in(cancellations))
        rs_query = rs_query.filter(Reschedule.date == day.date())

        return list(chain(sl_query.all(), rs_query.all()))

    def get_events_weekday(self, weekday: int):
        """Get events from the database."""
        sl_query = self.session.query(ScheduledLesson)
        rs_query = self.session.query(Reschedule)

        if self.user:
            sl_query = sl_query.filter(ScheduledLesson.user_id == self.user.id)
            rs_query = rs_query.filter(Reschedule.user_id == self.user.id)

        sl_query = sl_query.filter(ScheduledLesson.weekday == weekday)

        today = datetime.now(TIMEZONE).date()
        rs_query = rs_query.filter(Reschedule.date.is_not(None), Reschedule.date >= today)
        rs_query = [r for r in rs_query.all() if r.date.weekday() == weekday]

        return list(chain(sl_query.all(), rs_query))

    def model_list_adapter_user(self, models: Sequence[ScheduledLesson | Reschedule | RestrictedTime | WorkBreak]):
        """Convert list of models to list of dicts."""
        result = [model.edges for model in models if model.edges[0]]
        result.sort(key=lambda x: x[0])
        return result

    def model_list_adapter_teacher(self, models: Sequence[ScheduledLesson | Reschedule | RestrictedTime | WorkBreak]):
        """Convert list of models to list of dicts."""
        result = [(*model.edges, model.user.username_dog, model.user.telegram_id) for model in models if model.edges[0]]
        result.sort(key=lambda x: x[0])
        return result

    def get_avaiable_time(self, start: time, end: time, taken_times: list[tuple[time, time]]):
        """Get available time from start to end without taken times."""
        available = []
        current_time: time = start
        while current_time < end:
            taken = False
            for taken_time in taken_times:
                if taken_time[0] <= current_time < taken_time[1]:
                    taken = True
                    break
            if not taken:
                available.append(current_time)
            current_time = (
                current_time.replace(hour=current_time.hour + 1)
                if current_time.hour < MAX_HOUR
                else current_time.replace(hour=0)
            )
        return available

    def get_unavailable_weekdays(self, user_id: int):
        """Get unavailable weekdays."""
        user: User | None = self.session.query(User).get(user_id)
        if user is None:
            return []
        weekends = [w.weekday for w in user.teacher.weekends]
        restricted = [r.weekday for r in user.restricted_times if r.whole_day_restricted]
        return weekends + restricted

    def get_available_weekdays(self, user: User):
        """Get available weekdays."""
        teacher: Teacher = self.session.query(Teacher).get(user.teacher_id)
        na_weekdays = self.get_unavailable_weekdays(user.id)
        result = []
        for wd in range(7):
            if wd in na_weekdays:
                continue
            events = self.get_events_weekday(wd)  # MAYB HERE USER NEEDS TO BE NONE ALWAYS
            if self.get_avaiable_time(teacher.work_start, teacher.work_end, self.model_list_adapter_user(events)):
                result.append(wd)
        return result

    def get_available_days(self, user: User) -> list[datetime]:
        """Get available days."""
        teacher: Teacher = self.session.query(Teacher).get(user.teacher_id)
        na_weekdays = [w.weekday for w in teacher.weekends] + [
            r.weekday for r in user.restricted_times if r.whole_day_restricted
        ]
        result = []
        for wd in range(7):
            if wd in na_weekdays:
                continue

            # MAYB HERE USER NEEDS TO BE NONE ALWAYS
            if self.get_avaiable_time(teacher.work_start, teacher.work_end, self.get_events_day(wd)):
                result.append(wd)
        return result


class SecondFunctions(FirstFunctions):
    def schedule_day(self, day: datetime):
        """Get schedule for the day."""
        holidays = (
            self.session.query(Vacations)
            .filter(
                Vacations.start_date <= day.date(),
                Vacations.end_date >= day.date(),
                Vacations.teacher_id == self.user.teacher_id,
            )
            .all()
        )
        if holidays:
            return []
        events = self.get_events_day(day)
        events.sort(key=lambda x: x.start_time)
        result = []
        for e in events:
            if self.user:
                result.append(e.short_repr)
            else:
                result.append(e.long_repr)
        return [e.short_repr for e in events]

    def available_weekdays(self):
        """Get available weekdays."""
        return self.get_available_weekdays(self.user)

    def available_time_weekday(self, weekday: int):
        """Get available time for the weekday."""
        events = self.get_events_weekday(weekday)
        teacher: Teacher = self.session.query(Teacher).get(self.user.teacher_id)
        breaks = [wb.edges for wb in teacher.breaks if wb.weekday == weekday]
        model_list = self.model_list_adapter_user(events) if self.user else self.model_list_adapter_teacher(events)
        return self.get_avaiable_time(teacher.work_start, teacher.work_end, model_list + breaks)

    def available_time_day(self, day: datetime):
        """Get available time for the day."""
        events = self.get_events_day(day)
        teacher: Teacher = self.session.query(Teacher).get(self.user.teacher_id)
        breaks = [wb.edges for wb in teacher.breaks if wb.weekday == day.weekday()]
        model_list = self.model_list_adapter_user(events) if self.user else self.model_list_adapter_teacher(events)
        return self.get_avaiable_time(teacher.work_start, teacher.work_end, model_list + breaks)


class Schedule(SessionBase):
    DAY_SCHEDULE = "Занятия на %s:\n"
    DAY_EMPTY_SCHEDULE = "На %s занятий нет"
    EMPTY_SCHEDULE = "Занятий нет"

    WEEKDAY_MAP = {
        0: "ПН",
        1: "ВТ",
        2: "СР",
        3: "ЧТ",
        4: "ПТ",
        5: "СБ",
        6: "ВС",
    }
    WEEKDAY_MAP_FULL = {
        0: "Понедельник",
        1: "Вторник",
        2: "Среда",
        3: "Четверг",
        4: "Пятница",
        5: "Суббота",
        6: "Воскресенье",
    }

    def __init__(self, session: Session) -> None:
        """Initialize schedule class."""
        self.session = session

    @staticmethod
    def time_overlapse(borders1: Iterable[time], borders2: Iterable[time]):
        """Check if two time ranges overlap."""
        start1, end1 = borders1
        start2, end2 = borders2
        return start1 < end2 and start2 < end1

    def check_schedule_consistency(self, teacher: Teacher):
        """Check schedule consistency. E.g. if lessons collide - return these lessons."""
        weekends = [we.weekday for we in WeekendRepo(self.session).all(teacher)]
        work_beaks = {wb.weekday: (wb.start_time, wb.end_time) for wb in WorkBreakRepo(self.session).all(teacher)}
        lessons: list[Reschedule | ScheduledLesson] = []
        for s in teacher.students:
            lessons.extend(LessonCollectionRepo(self.session).all(s))
        # TODO Лишние уроки выдаются
        collisions = Collisions()
        for lesson in lessons:
            if isinstance(lesson, Reschedule) and lesson.date is None:
                continue
            if not self.time_overlapse(lesson.edges, (teacher.work_start, teacher.work_end)):
                collisions.work_borders.append(lesson)
            elif lesson.weekday in weekends:
                collisions.weekends.append(lesson)
            elif lesson.weekday in work_beaks and self.time_overlapse(work_beaks[lesson.weekday], lesson.edges):
                collisions.work_breaks.append(lesson)
        return collisions

    def lessons_day(self, user: User, date: date, teacher: Teacher | None = None):
        """Get lessons for day."""
        reschedules = self.session.query(Reschedule).filter(Reschedule.date == date).all()
        lessons = self.session.query(Lesson).filter(Lesson.date == date).all()
        cancellations = [c.id for c in self.session.query(Reschedule).filter(Reschedule.source_date == date).all()]
        scheduled_lessons = (
            self.session.query(ScheduledLesson)
            .filter(
                ScheduledLesson.weekday == date.weekday(),
                ScheduledLesson.id.not_in(cancellations),
            )
            .all()
        )

        if not teacher:
            events: list[ScheduledLesson | Lesson | Reschedule] = [
                *[sl for sl in scheduled_lessons if sl.user_id == user.id],
                *[r for r in reschedules if r.user_id == user.id],
                *[lsn for lsn in lessons if lsn.user_id == user.id],
            ]
        else:
            students_ids = [s.id for s in teacher.students]
            events: list[ScheduledLesson | Lesson | Reschedule] = [
                *[sl for sl in scheduled_lessons if sl.user_id in students_ids],
                *[r for r in reschedules if r.user_id in students_ids],
                *[lsn for lsn in lessons if lsn.user_id in students_ids],
            ]
        return sorted(events, key=lambda x: x.edges[0])

    def lessons_day_message(self, user: User, date: date):
        """Get message with lessons for day."""
        teacher = TeacherRepo(self.session).get(user.teacher_id)
        if teacher:
            lessons = self.lessons_day(user, date, teacher)
        if lessons:
            if teacher:
                lessons_text = "\n".join([lesson.long_repr for lesson in lessons])
            else:
                lessons_text = "\n".join([lesson.short_repr for lesson in lessons])
            return self.DAY_SCHEDULE % date.strftime("%d.%m.%Y") + lessons_text
        return self.DAY_EMPTY_SCHEDULE % date.strftime("%d.%m.%Y")

    def lessons_week(self, user: User, start_date: date):
        """Get lessons for week."""
        week = [date(start_date.year, start_date.month, day) for day in range(start_date.day, start_date.day + 7)]
        for day in week:
            yield self.lessons_day(user, day)

    def lessons_week_message(self, user: User, start_date: date):
        """Get message with lessons for week."""
        week = [date(start_date.year, start_date.month, day) for day in range(start_date.day, start_date.day + 7)]
        days = []
        for day in week:
            day_schedule = self.lessons_day(user, day)
            weekday = html.bold(self.WEEKDAY_MAP_FULL[day.weekday()])
            current_date = f" {day.strftime("%d.%m.%Y")}\n"
            if day_schedule:
                current_day_schedule = [lesson.short_repr for lesson in day_schedule]
                days.append(weekday + current_date + "\n".join(current_day_schedule))
            else:
                days.append(weekday + current_date + self.EMPTY_SCHEDULE)
        return "\n\n".join(days)

    def available_weekdays(self):
        """Get available weekdays."""

    def available_time(self):
        pass

    def add_lesson(self):
        pass
