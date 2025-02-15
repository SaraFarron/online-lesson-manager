from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from itertools import chain

from aiogram import html
from sqlalchemy.orm import Session
import calendar
from config.config import HRS_TO_CANCEL, TIMEZONE
from models import Lesson, Reschedule, RestrictedTime, ScheduledLesson, Teacher, User, Vacations, WorkBreak
from repositories import LessonCollectionRepo, TeacherRepo, WeekendRepo, WorkBreakRepo, RescheduleRepo, VacationsRepo

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


class EventsService(SessionBase):
    @staticmethod
    def week_from_date(start_of_week: date):
        """Get dates for the current week from start."""
        end_of_week = start_of_week + timedelta(days=7)
        return [start_of_week + timedelta(n) for n in range(int((end_of_week - start_of_week).days))]

    def closest_date_from_weekday(self, weekday: int, start: date):
        for d in self.week_from_date(start):
            if d.weekday() == weekday:
                return d

    def is_too_late_to_cancel(self, event_time: time, event_date: date):
        now = datetime.now(TIMEZONE)
        if now.date() > event_date:
            return True
        if now.date() < event_date:
            return False
        return event_time < now.time().replace(hour=now.time().hour + HRS_TO_CANCEL)

    @staticmethod
    def time_overlapse(borders1: Iterable[time], borders2: Iterable[time]):
        """Check if two time ranges overlap."""
        start1, end1 = borders1
        start2, end2 = borders2
        return start1 < end2 and start2 < end1

    def lessons_day(self, user: User, day: date, teacher: Teacher | None = None):
        """Get lessons for day."""
        active_vacations = VacationsRepo(self.session).get_active_vacations(user, day)
        if active_vacations:
            return []

        reschedules = self.session.query(Reschedule).filter(Reschedule.date == day).all()
        lessons = self.session.query(Lesson).filter(Lesson.date == day).all()
        cancellations = [c.source_id for c in
                         self.session.query(Reschedule).filter(Reschedule.source_date == day).all()]
        scheduled_lessons = (
            self.session.query(ScheduledLesson)
            .filter(
                ScheduledLesson.weekday == day.weekday(),
                ScheduledLesson.id.not_in(cancellations),
            )
            .all()
        )
        reschedules_times = [r.start_time for r in reschedules]

        if not teacher:
            events: list[ScheduledLesson | Lesson | Reschedule] = [
                *[sl for sl in scheduled_lessons if sl.user_id == user.id and sl.start_time not in reschedules_times],
                *[r for r in reschedules if r.user_id == user.id],
                *[lsn for lsn in lessons if lsn.user_id == user.id],
            ]
        else:
            students_ids = [s.id for s in teacher.students]
            events: list[ScheduledLesson | Lesson | Reschedule] = [
                *[sl for sl in scheduled_lessons if
                  sl.user_id in students_ids and sl.start_time not in reschedules_times],
                *[r for r in reschedules if r.user_id in students_ids],
                *[lsn for lsn in lessons if lsn.user_id in students_ids],
            ]
        return sorted(events, key=lambda x: x.edges[0])

    def lessons_week(self, user: User, start_date: date):
        """Get lessons for week."""
        week = [date(start_date.year, start_date.month, day) for day in range(start_date.day, start_date.day + 7)]
        teacher = TeacherRepo(self.session).get_by_telegram_id(user.telegram_id)
        for day in week:
            yield self.lessons_day(user, day, teacher)

    def events_weekday(self, wd: int, teacher: Teacher, user: User | None = None):
        """Get events for weekday."""
        teacher_weekends = [w.weekday for w in teacher.weekends]
        filters = (ScheduledLesson.weekday == wd, ScheduledLesson.weekday.not_in(teacher_weekends))
        if user:
            filters += (ScheduledLesson.user_id == user.id,)
        return self.session.query(ScheduledLesson).filter(*filters).all()

    def events_date(self, day: date, teacher: Teacher, user: User | None = None):
        """Get events for date."""
        teacher_weekends = (
            self.session.query(Vacations)
            .filter(Vacations.teacher_id == teacher.id, Vacations.start_date <= day, Vacations.end_date >= day)
            .all()
        )
        if teacher_weekends:
            return []
        filters = (Reschedule.date == day,)
        if user:
            filters += (Reschedule.user_id == user.id,)
        reschedules = self.session.query(Reschedule).filter(*filters).all()

        cancellations = [r.source_id for r in self.session.query(Reschedule).filter(Reschedule.date == day).all()]
        scheduled_lessons = (
            self.session.query(ScheduledLesson)
            .filter(ScheduledLesson.weekday == day.weekday(), ScheduledLesson.id.not_in(cancellations))
            .all()
        )
        if user:
            reschedules = [r for r in reschedules if r.user_id == user.id]
            scheduled_lessons = [sl for sl in scheduled_lessons if sl.user_id == user.id]

        return sorted(reschedules + scheduled_lessons, key=lambda x: x.edges[0])

    def events_day(self, day: date | int, teacher: Teacher, user: User | None = None):
        """Get events for day."""
        if isinstance(day, int):
            return self.events_weekday(day, teacher, user)
        return self.events_date(day, teacher, user)

    def available_times(self, start: time, end: time, taken_times: list[tuple[time, time]]):
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

    def is_available_weekday(self, wd: int, teacher: Teacher):
        """Check if weekday has available time."""
        sl_query = self.session.query(ScheduledLesson)
        rs_query = self.session.query(Reschedule)

        sl_query = sl_query.filter(ScheduledLesson.weekday == wd)

        today = datetime.now(TIMEZONE).date()
        rs_query = rs_query.filter(Reschedule.date.is_not(None), Reschedule.date >= today)
        rs_query = [r for r in rs_query.all() if r.date.weekday() == wd]  # type: ignore  # noqa: PGH003

        events = [model.edges for model in list(chain(sl_query.all(), rs_query)) if model.edges[0]]
        events.sort(key=lambda x: x[0])
        return bool(self.available_times(teacher.work_start, teacher.work_end, events))


class Schedule(EventsService):
    DAY_SCHEDULE = "Занятия на %s:"
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

    def check_schedule_consistency(self, teacher: Teacher):
        """Check schedule consistency. E.g. if lessons collide - return these lessons."""
        weekends = [we.weekday for we in WeekendRepo(self.session).all(teacher)]
        work_beaks = {wb.weekday: (wb.start_time, wb.end_time) for wb in WorkBreakRepo(self.session).all(teacher)}
        lessons: list[Reschedule | ScheduledLesson] = []
        for s in teacher.students:
            lessons.extend(LessonCollectionRepo(self.session).all(s))
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

    def lessons_day_message(self, user: User, day: date):
        """Get message with lessons for day."""
        teacher = TeacherRepo(self.session).get_by_telegram_id(user.telegram_id)
        lessons = self.lessons_day(user, day, teacher)
        if lessons:
            if teacher:
                lessons_text = "\n".join([lesson.long_repr for lesson in lessons])
            else:
                lessons_text = "\n".join([lesson.short_repr for lesson in lessons])
            return self.DAY_SCHEDULE % day.strftime("%d.%m.%Y") + "\n" + lessons_text
        return self.DAY_EMPTY_SCHEDULE % day.strftime("%d.%m.%Y")

    def lessons_week_message(self, user: User, start_date: date):
        """Get message with lessons for week."""

        days_to_subtract = start_date.weekday()  # Monday is 0, Sunday is 6
        monday_start = start_date - timedelta(days=days_to_subtract)

        week = [monday_start + timedelta(days=i) for i in range(7)]
        teacher = TeacherRepo(self.session).get_by_telegram_id(user.telegram_id)
        days = []
        for day in week:
            day_schedule = self.lessons_day(user, day, teacher)
            weekday = html.bold(self.WEEKDAY_MAP_FULL[day.weekday()])
            current_date = f" {day.strftime("%d.%m.%Y")}\n"
            if day_schedule:
                if teacher:
                    current_day_schedule = [lesson.long_repr for lesson in day_schedule]
                else:
                    current_day_schedule = [lesson.short_repr for lesson in day_schedule]
                days.append(weekday + current_date + "\n".join(current_day_schedule))
            else:
                days.append(weekday + current_date + self.EMPTY_SCHEDULE)
        return "\n\n".join(days)

    def available_weekdays(self, user: User) -> list[int]:
        """Get available weekdays."""
        teacher = TeacherRepo(self.session).get(user.teacher_id)
        if not teacher:
            msg = f"Teacher {user.teacher_id} not found"
            raise ValueError(msg)

        weekends = [w.weekday for w in user.teacher.weekends]
        restricted = [r.weekday for r in user.restricted_times if r.whole_day_restricted]
        na_weekdays = weekends + restricted

        result = []
        for wd in range(7):
            if wd in na_weekdays or not self.is_available_weekday(wd, teacher):
                continue
            result.append(wd)
        return result

    def available_time(self, user: User, day: date | int) -> list[time]:
        """Get available time for day."""
        vac_repo = VacationsRepo(self.session)
        if isinstance(day, date):
            work_breaks = WorkBreakRepo(self.session).get_many(WorkBreak.weekday == day.weekday())
            teacher_weekends = (
                self.session.query(Vacations)
                .filter(Vacations.teacher_id == user.teacher.id, Vacations.start_date <= day, Vacations.end_date >= day)
                .all()
            )
            if teacher_weekends:
                return []
            events = []
            for e in self.events_day(day, user.teacher, None):
                if vac_repo.get_active_vacations(e.user, day):
                    continue
                events.append((e.start_time, e.end_time))
        else:
            work_breaks = WorkBreakRepo(self.session).get_many(WorkBreak.weekday == day)
            events = [(e.start_time, e.end_time) for e in self.events_day(day, user.teacher, None)]
        events += [(wb.start_time, wb.end_time) for wb in work_breaks]
        return self.available_times(user.teacher.work_start, user.teacher.work_end, events)

    def available_time_with_reschedules(self, user: User, day: date | int) -> list[tuple[time, str]]:
        """Get available time for day with consideration for reschedules."""
        available_time = self.available_time(user, day)
        res_repo = RescheduleRepo(self.session)
        vac_repo = VacationsRepo(self.session)
        time_with_reschedules = []
        for t in available_time:
            if isinstance(day, date):
                reschedule = res_repo.get_by_where(
                    (Reschedule.date == day, Reschedule.user_id == user.id, Reschedule.start_time == t))
            else:
                today = datetime.now(TIMEZONE).date()
                closest_date = self.closest_date_from_weekday(day, today)
                reschedule = res_repo.get_by_where(
                    (Reschedule.date == closest_date, Reschedule.start_time == t))
            if reschedule:
                if not vac_repo.get_active_vacations(reschedule.user, reschedule.date):
                    time_with_reschedules.append((t, f"Занято {reschedule.date}"))
            else:
                time_with_reschedules.append((t, ""))
        return time_with_reschedules

    def events_to_cancel(self, user: User, day: date):
        """Get events available to cancel."""
        scheduled_lessons = self.session.query(ScheduledLesson).filter(ScheduledLesson.user_id == user.id).all()
        reschedules = (
            self.session.query(Reschedule)
            .filter(
                Reschedule.user_id == user.id,
                Reschedule.date.is_not(None),
                Reschedule.date >= day,
            )
            .all()
        )
        lessons = self.session.query(Lesson).filter(Lesson.user_id == user.id, Lesson.date >= day).all()
        events = [r for r in reschedules if
                  not self.is_too_late_to_cancel(r.start_time, r.date)]  # type: ignore  # noqa: PGH003
        return sorted(events + scheduled_lessons + lessons, key=lambda e: e.start_time)
