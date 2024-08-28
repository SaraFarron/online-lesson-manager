from __future__ import annotations

from datetime import datetime, timedelta


def get_weeks(start_date: datetime | None = None):
    if start_date is None:
        start_date = datetime.now()
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
                    "date": date.strftime("%m.%d"),
                },
            )
        weeks.append(week)
    return weeks
