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
        day_start = f"Начало: {day['start']}\n"
        day_break = f"Перерыв: {day['break']['start']} - {day['break']['end']}\n" if "break" in day else ""
        day_end = f"Конец: {day['end']}"
        days[key] = f"{day_start}{day_break}{day_end}"
    return days


def working_hours_on_day(weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]):
    """Get working schedule for the current day."""
    with open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
        data: dict[str, str] = json.load(f)
    if weekday in data:
        return {weekday: data[weekday]}
    return {weekday: messages.NO_DATA}
