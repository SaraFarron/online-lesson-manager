from __future__ import annotations

from datetime import datetime, timedelta

import pytz
from sqlalchemy.orm import Session

from src.database import engine
from src.models import Lesson


def get_weeks(start_date: datetime | None = None):
    """Get a list of the week days for the next 4 weeks."""
    if start_date is None:
        start_date = datetime.now(pytz.timezone("Europe/Moscow"))
    weeks = []
    for i in range(4):
        week_start = start_date + timedelta(days=i * 7)
        week = []
        for day in range(7):
            date = week_start + timedelta(days=day)
            weekday = date.strftime("%A").lower()
            week.append(
                {
                    "weekday": weekday,
                    "date": date.strftime("%d.%m"),
                },
            )
        weeks.append(week)
    return weeks


def get_available_time(date: datetime) -> list[tuple[int, int]]:
    """Get a list of available time for the current day."""
    with Session(engine) as session:
        # Get all lessons for the current day
        lessons = session.query(Lesson).filter(
            Lesson.date == date.date(),
            Lesson.status == "upcoming",
        ).all()

        # Create a set of all times that are taken
        taken_times = {(lesson.time.hour, lesson.time.minute) for lesson in lessons}

        # Create a list of available times
        available_times = []
        for hour in range(24):
            for minute in range(0, 60, 30):
                if (hour, minute) not in taken_times:
                    available_times.append((hour, minute))

    return available_times
