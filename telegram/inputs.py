import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram.api import LocalIntervalResolution

_DATE_PATTERNS = (
    re.compile(
        r"(?P<month>[0-9]{1,2})\s+(?P<day>[0-9]{1,2})(?:\s+(?P<year>[0-9]{4}))?"
    ),
    re.compile(r"(?P<month>[0-9]{1,2}):(?P<day>[0-9]{1,2})(?::(?P<year>[0-9]{4}))?"),
    re.compile(r"(?P<month>[0-9]{1,2})/(?P<day>[0-9]{1,2})(?:/(?P<year>[0-9]{4}))?"),
)
_SCHEDULE_CLOCK_PATTERN = re.compile(r"[0-9]{1,2}(?::| )[0-9]{1,2}")


@dataclass(frozen=True)
class ParsedScheduleDate:
    value: date
    inferred_year: bool

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
            "Enter a date as MM DD, MM:DD or MM/DD, optionally followed by a year."
        )

    month, day = int(match["month"]), int(match["day"])
    explicit_year = match["year"]
    if explicit_year is not None:
        return ParsedScheduleDate(
            _calendar_date(int(explicit_year), month, day), inferred_year=False
        )

    candidate = _calendar_date(today.year, month, day)
    if candidate < today:
        try:
            candidate = _calendar_date(today.year + 1, month, day)
        except ValueError as exc:
            raise ValueError(
                "This date does not occur next year; enter an explicit year."
            ) from exc
        return ParsedScheduleDate(candidate, inferred_year=True)
    return ParsedScheduleDate(candidate, inferred_year=False)


def _calendar_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Enter a valid calendar date.") from exc


def format_schedule_date_confirmation(parsed: ParsedScheduleDate) -> str:
    if parsed.requires_confirmation:
        return (
            f"{parsed.value.isoformat()} is in the next year. "
            "Confirm this date or go back."
        )
    return f"Date: {parsed.value.isoformat()}"


def parse_schedule_time(value: str) -> str:
    value = value.strip()
    if _SCHEDULE_CLOCK_PATTERN.fullmatch(value) is None:
        raise ValueError("Enter a time as HH:MM or HH MM.")
    return parse_clock(value)


def combine_schedule_date_time(day: date, clock: str) -> datetime:
    hour, minute = (int(part) for part in parse_schedule_time(clock).split(":"))
    return datetime.combine(day, time(hour, minute))


def format_local_interval_review(
    resolution: LocalIntervalResolution, *, duration_minutes: int | None = None
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
        start_offset = start_local.strftime("%z")
        end_offset = end_local.strftime("%z")
        return (
            f"Timezone: {resolution.timezone}\n"
            f"Start: {start_local:%Y-%m-%d %H:%M} "
            f"(UTC{start_offset[:3]}:{start_offset[3:]})\n"
            f"End: {end_local:%Y-%m-%d %H:%M} "
            f"(UTC{end_offset[:3]}:{end_offset[3:]})\n"
            f"UTC interval: {start.utc.isoformat()} - {end_utc.isoformat()}"
        )
    return (
        f"Timezone: {resolution.timezone}\n"
        f"Start: {start.local:%Y-%m-%d %H:%M} (UTC{start.utc_offset})\n"
        f"End: {end.local:%Y-%m-%d %H:%M} (UTC{end.utc_offset})\n"
        f"UTC interval: {start.utc.isoformat()} - {end.utc.isoformat()}"
    )


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
