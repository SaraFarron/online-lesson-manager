import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
