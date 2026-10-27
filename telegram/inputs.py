import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram.api import LocalIntervalResolution

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_SHORT_MONTH_NAMES = tuple(month[:3] for month in _MONTH_NAMES)
_SHORT_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DATE_PATTERNS = (
    re.compile(
        r"(?P<day>[0-9]{1,2})\s+(?P<month>[0-9]{1,2})(?:\s+(?P<year>[0-9]{4}))?"
    ),
    re.compile(r"(?P<day>[0-9]{1,2}):(?P<month>[0-9]{1,2})(?::(?P<year>[0-9]{4}))?"),
    re.compile(r"(?P<day>[0-9]{1,2})/(?P<month>[0-9]{1,2})(?:/(?P<year>[0-9]{4}))?"),
)
_SCHEDULE_CLOCK_PATTERN = re.compile(r"[0-9]{1,2}(?::| )[0-9]{1,2}")


@dataclass(frozen=True)
class ParsedScheduleDate:
    value: date
    inferred_year: bool
    reference_date: date

    @property
    def requires_confirmation(self) -> bool:
        return self.inferred_year


def parse_schedule_date(value: str, today: date) -> ParsedScheduleDate:
    value = value.strip()
    match = None
    for pattern in _DATE_PATTERNS:
        match = pattern.fullmatch(value)
        if match is not None:
            break
    if match is None:
        raise ValueError(
            "Enter a date as DD MM, DD:MM or DD/MM, optionally followed by a year."
        )

    day, month = int(match["day"]), int(match["month"])
    explicit_year = match["year"]
    if explicit_year is not None:
        return ParsedScheduleDate(
            _calendar_date(int(explicit_year), month, day),
            inferred_year=False,
            reference_date=today,
        )

    candidate = _calendar_date(today.year, month, day)
    if candidate < today:
        try:
            candidate = _calendar_date(today.year + 1, month, day)
        except ValueError as exc:
            raise ValueError(
                "This date does not occur next year; enter an explicit year."
            ) from exc
        return ParsedScheduleDate(candidate, inferred_year=True, reference_date=today)
    return ParsedScheduleDate(candidate, inferred_year=False, reference_date=today)


def _calendar_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Enter a valid calendar date.") from exc


def format_schedule_date_confirmation(parsed: ParsedScheduleDate) -> str:
    human_date = format_human_date(parsed.value, reference_date=parsed.reference_date)
    if parsed.requires_confirmation:
        return f"{human_date} is in the next year. Confirm this date or go back."
    return f"Date: {human_date}"


def format_human_date(value: date, *, reference_date: date) -> str:
    year = f" ({value.year})" if value.year != reference_date.year else ""
    return f"{_MONTH_NAMES[value.month - 1]} {value.day}{year}"


def format_human_datetime(value: datetime, *, reference_date: date) -> str:
    year = f" ({value.year})" if value.year != reference_date.year else ""
    return (
        f"{_MONTH_NAMES[value.month - 1]} {value.day} "
        f"at {format_human_time(value)}{year}"
    )


def format_human_time(value: datetime) -> str:
    return f"{_format_clock(value)}{_format_fold(value)}"


def format_compact_schedule_date(value: date, *, reference_date: date) -> str:
    year = f" ({value.year})" if value.year != reference_date.year else ""
    return (
        f"{_SHORT_WEEKDAY_NAMES[value.weekday()]}, "
        f"{_SHORT_MONTH_NAMES[value.month - 1]} {value.day}{year}"
    )


def format_local_interval(
    start: datetime, end: datetime, *, reference_date: date
) -> str:
    start_date = format_human_date(start.date(), reference_date=reference_date)
    start_clock = f"{_format_clock(start)}{_format_fold(start)}"
    end_clock = f"{_format_clock(end)}{_format_fold(end)}"
    if start.date() == end.date():
        return f"{start_date} {start_clock} - {end_clock}"
    end_date = format_human_date(end.date(), reference_date=reference_date)
    return f"{start_date} {start_clock} - {end_date} {end_clock}"


def format_human_datetime_in_timezone(
    value: datetime, timezone: str, *, reference_date: date
) -> str:
    return format_human_datetime(
        value.astimezone(ZoneInfo(timezone)), reference_date=reference_date
    )


def format_local_interval_in_timezone(
    start: datetime, end: datetime, timezone: str, *, reference_date: date
) -> str:
    zone = ZoneInfo(timezone)
    return format_local_interval(
        start.astimezone(zone), end.astimezone(zone), reference_date=reference_date
    )


def _format_clock(value: datetime) -> str:
    return f"{value.hour}:{value.minute:02d}"


def _format_fold(value: datetime) -> str:
    if value.tzinfo is None:
        return ""
    first = value.replace(fold=0).utcoffset()
    second = value.replace(fold=1).utcoffset()
    if first == second:
        return ""
    return " (first occurrence)" if value.fold == 0 else " (second occurrence)"


def parse_schedule_time(value: str) -> str:
    value = value.strip()
    if _SCHEDULE_CLOCK_PATTERN.fullmatch(value) is None:
        raise ValueError("Enter a time as HH:MM or HH MM.")
    return parse_clock(value)


def combine_schedule_date_time(day: date, clock: str) -> datetime:
    hour, minute = (int(part) for part in parse_schedule_time(clock).split(":"))
    return datetime.combine(day, time(hour, minute))


def format_local_interval_review(
    resolution: LocalIntervalResolution,
    *,
    reference_date: date,
    duration_minutes: int | None = None,
) -> str:
    start = resolution.start
    end = resolution.end
    if duration_minutes is not None:
        try:
            end_utc = start.utc + timedelta(minutes=duration_minutes)
        except OverflowError as exc:
            raise ValueError("Lesson duration is outside the supported range.") from exc
        if end_utc <= start.utc:
            raise ValueError("Lesson duration must be positive.")
        zone = ZoneInfo(resolution.timezone)
        start_local = start.utc.astimezone(zone)
        end_local = end_utc.astimezone(zone)
    else:
        zone = ZoneInfo(resolution.timezone)
        start_local = start.utc.astimezone(zone)
        end_local = end.utc.astimezone(zone)
    interval = format_local_interval(
        start_local, end_local, reference_date=reference_date
    )
    return f"{interval}\nTimezone: {resolution.timezone}"


def parse_timezone(value: str) -> str:
    value = value.strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            "Enter a valid IANA timezone, for example Asia/Bangkok."
        ) from exc
    return value


def parse_clock(value: str) -> str:
    match = re.fullmatch(r"([0-9]{1,2})[ :/]([0-9]{1,2})", value.strip())
    if match is None:
        raise ValueError("Enter a time as HH:MM, HH MM or HH/MM.")
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        raise ValueError("Hour must be 0–23 and minute 0–59.")
    return f"{hour:02d}:{minute:02d}"


def parse_integer(value: str, minimum: int = 1, maximum: int = 2**31 - 1) -> int:
    if re.fullmatch(r"[0-9]+", value.strip()) is None:
        raise ValueError("Enter a whole number.")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"Value must be between {minimum} and {maximum}.")
    return number


def digest_clock(value: int | None) -> str:
    if value is None:
        return "off"
    return f"{value // 60:02d}:{value % 60:02d}"
