from __future__ import annotations

import json
from typing import Literal

from config import messages
from config.config import WORK_SCHEDULE_TIMETABLE_PATH


def working_hours() -> dict[str, str]:
    """Get working schedule."""
    with open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
        data: dict[str, str] = json.load(f)
    days = {}
    for key, day in data.items():
        working_hours = f"Рабочее время: {day['start']} - {day['end']}"
        day_break = f"\nПерерыв: {day['break']['start']} - {day['break']['end']}" if "break" in day else ""
        days[key] = working_hours + day_break
    return days


def working_hours_on_day(weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]):
    """Get working schedule for the current day."""
    with open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
        data: dict[str, str] = json.load(f)
    if weekday in data:
        return {weekday: data[weekday]}
    return {weekday: messages.NO_DATA}
