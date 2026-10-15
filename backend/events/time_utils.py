from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.exceptions import ValidationError


def normalize_utc(value: datetime) -> datetime:
    """Normalize an API/ORM datetime to aware UTC; naive values mean UTC."""
    if value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_storage_utc(value: datetime) -> datetime:
    """Return a naive UTC datetime suitable for the event DateTime columns."""
    return normalize_utc(value).replace(tzinfo=None)


def from_storage_utc(value: datetime) -> datetime:
    """Load a naive UTC database value as aware UTC."""
    return normalize_utc(value)


def get_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"Invalid IANA timezone: {timezone_name}") from exc


def resolve_local_datetime(value: datetime, timezone_name: str) -> datetime:
    """Resolve a naive local datetime using the approved gap/fold policy."""
    if value.utcoffset() is not None:
        raise ValidationError("Local datetime must not include a timezone offset")

    zone = get_timezone(timezone_name)
    candidates = _valid_local_candidates(value, zone)
    if candidates:
        return min(candidates, key=lambda candidate: candidate.astimezone(UTC))

    before = value.replace(tzinfo=zone, fold=0).utcoffset()
    after = value.replace(tzinfo=zone, fold=1).utcoffset()
    if before is None or after is None:
        raise ValidationError("Could not resolve local datetime")

    gap = after - before
    if gap <= timedelta(0):
        raise ValidationError("Could not resolve nonexistent local datetime")

    try:
        shifted = value + gap
    except OverflowError as exc:
        raise ValidationError("Local datetime is outside the supported range") from exc

    shifted_candidates = _valid_local_candidates(shifted, zone)
    if not shifted_candidates:
        raise ValidationError("Could not resolve local datetime after DST gap")
    return min(shifted_candidates, key=lambda candidate: candidate.astimezone(UTC))


def _valid_local_candidates(value: datetime, zone: ZoneInfo) -> list[datetime]:
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value:
            candidates.append(candidate)
    return candidates


def local_day_bounds(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    try:
        next_day = day + timedelta(days=1)
    except OverflowError as exc:
        raise ValidationError("Date is outside the supported range") from exc

    start = _local_midnight_utc(day, timezone_name)
    end = _local_midnight_utc(next_day, timezone_name)
    if start >= end:
        raise ValidationError("Local day has no representable UTC range")
    return start, end


def local_week_bounds(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    monday = day - timedelta(days=day.weekday())
    try:
        next_monday = monday + timedelta(days=7)
    except OverflowError as exc:
        raise ValidationError("Date is outside the supported range") from exc

    start = _local_midnight_utc(monday, timezone_name)
    end = _local_midnight_utc(next_monday, timezone_name)
    if start >= end:
        raise ValidationError("Local week has no representable UTC range")
    return start, end


def _local_midnight_utc(day: date, timezone_name: str) -> datetime:
    return normalize_utc(
        resolve_local_datetime(datetime.combine(day, time.min), timezone_name)
    )


def intervals_overlap(
    start: datetime,
    end: datetime,
    other_start: datetime,
    other_end: datetime,
) -> bool:
    start_utc = normalize_utc(start)
    end_utc = normalize_utc(end)
    other_start_utc = normalize_utc(other_start)
    other_end_utc = normalize_utc(other_end)

    if start_utc >= end_utc or other_start_utc >= other_end_utc:
        raise ValidationError("Time intervals must have start before end")
    return start_utc < other_end_utc and end_utc > other_start_utc
