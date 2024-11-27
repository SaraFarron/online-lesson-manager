from __future__ import annotations

from collections.abc import Iterable
from datetime import date, time

from aiogram import html
from sqlalchemy.orm import Session

from models import Lesson, Reschedule, ScheduledLesson, Teacher, User
from repositories import LessonCollectionRepo, TeacherRepo, WeekendRepo, WorkBreakRepo


class Collisions:
    def __init__(self) -> None:
        self.weekends: list[ScheduledLesson | Reschedule] = []
        self.work_breaks: list[ScheduledLesson | Reschedule] = []
        self.work_borders: list[ScheduledLesson | Reschedule] = []

    @property
    def all_collisions(self) -> list[ScheduledLesson | Reschedule]:
        """Return all collisions."""
        return self.weekends + self.work_breaks

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


class Schedule:
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

    def lessons_day(self, user: User, date: date):
        """Get lessons for day."""
        teacher: Teacher | None = TeacherRepo(self.session).get(user.teacher_id)
        if teacher:
            lessons: list[Reschedule | ScheduledLesson | Lesson] = []
            for student in teacher.students:
                student_lessons = LessonCollectionRepo(self.session).all(student, date)
                lessons.extend(student_lessons)
            return sorted(lessons, key=lambda x: x.edges[0])
        lessons = LessonCollectionRepo(self.session).all(user, date)
        return sorted(lessons, key=lambda x: x.edges[0])

    def lessons_day_message(self, user: User, date: date):
        """Get message with lessons for day."""
        lessons = self.lessons_day(user, date)
        if lessons:
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
            current_date = day.strftime("%d.%m.%Y")
            if day_schedule:
                current_day_schedule = [lesson.short_repr for lesson in day_schedule]
                days.append(weekday + current_date + "\n".join(current_day_schedule))
            else:
                days.append(weekday + current_date + self.EMPTY_SCHEDULE)
        return "\n\n".join(days)
