from __future__ import annotations

from collections.abc import Iterable
from datetime import time

from sqlalchemy.orm import Session

from models import Reschedule, ScheduledLesson, Teacher
from repositories import LessonCollectionRepo, WeekendRepo, WorkBreakRepo


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
