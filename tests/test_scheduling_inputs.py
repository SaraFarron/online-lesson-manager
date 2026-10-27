from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from telegram.api import (
    BackendClient,
    LocalIntervalResolution,
    Registration,
    ResolvedLocalInstant,
)
from telegram.inputs import (
    combine_schedule_date_time,
    format_compact_schedule_date,
    format_duration,
    format_human_datetime,
    format_human_datetime_in_timezone,
    format_local_interval,
    format_local_interval_in_timezone,
    format_local_interval_review,
    format_schedule_date_confirmation,
    format_working_days,
    parse_schedule_date,
    parse_schedule_time,
)


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        ([5], "Fri"),
        ([1, 2], "Mon, Tue"),
        ([2, 3, 4, 5, 6, 7], "Tue - Sun"),
        ([1, 2, 3, 5, 6, 7], "Mon - Wed, Fri - Sun"),
        ([1, 2, 3, 6, 7], "Mon - Wed, Sat, Sun"),
        ([1, 2, 3, 4, 5, 6, 7], "Mon - Sun"),
        ([7, 3, 1, 6, 2], "Mon - Wed, Sat, Sun"),
    ],
)
def test_working_days_are_grouped_in_iso_order(days: list[int], expected: str) -> None:
    assert format_working_days(days) == expected


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, "0 minutes"),
        (1, "1 minute"),
        (45, "45 minutes"),
        (60, "1 hour"),
        (61, "1 hour 1 minute"),
        (90, "1 hour 30 minutes"),
        (120, "2 hours"),
    ],
)
def test_duration_uses_human_hours_and_minutes(minutes: int, expected: str) -> None:
    assert format_duration(minutes) == expected


def test_duration_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        format_duration(-1)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("05 11", date(2035, 11, 5)),
        ("05:11", date(2035, 11, 5)),
        ("05/11", date(2035, 11, 5)),
        ("5/11", date(2035, 11, 5)),
        ("05 11 2036", date(2036, 11, 5)),
        ("05:11:2036", date(2036, 11, 5)),
        ("05/11/2036", date(2036, 11, 5)),
    ],
)
def test_schedule_date_grammar(value: str, expected: date) -> None:
    parsed = parse_schedule_date(value, date(2035, 1, 1))
    assert parsed.value == expected
    assert not parsed.requires_confirmation


def test_schedule_date_infers_next_year_and_requires_confirmation() -> None:
    parsed = parse_schedule_date("02/01", date(2035, 12, 30))

    assert parsed.value == date(2036, 1, 2)
    assert parsed.inferred_year
    assert parsed.requires_confirmation
    assert (
        format_schedule_date_confirmation(parsed)
        == "January 2 (2036) is in the next year. Confirm this date or go back."
    )


def test_explicit_past_date_is_preserved_and_leap_days_are_checked() -> None:
    past = parse_schedule_date("03/04/2034", date(2035, 3, 5))
    leap_day = parse_schedule_date("29/02/2024", date(2025, 1, 1))
    upcoming_leap_day = parse_schedule_date("29/02", date(2024, 2, 28))

    assert past.value == date(2034, 4, 3)
    assert leap_day.value == date(2024, 2, 29)
    assert upcoming_leap_day.value == date(2024, 2, 29)
    assert not past.requires_confirmation


@pytest.mark.parametrize(
    ("value", "today", "message"),
    [
        ("30/02", date(2035, 1, 1), "valid calendar date"),
        ("01/13", date(2035, 1, 1), "valid calendar date"),
        ("29/02", date(2025, 3, 1), "valid calendar date"),
        ("29/02", date(2024, 3, 1), "explicit year"),
        ("11/25", date(2035, 1, 1), "valid calendar date"),
        ("01/01", date.max, "explicit year"),
        ("03-04", date(2035, 1, 1), "Enter a date"),
        ("03/04/10000", date(2035, 1, 1), "Enter a date"),
    ],
)
def test_invalid_or_unrepresentable_schedule_dates(
    value: str, today: date, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_schedule_date(value, today)


def test_ambiguous_schedule_date_is_unambiguously_day_first() -> None:
    parsed = parse_schedule_date("04/03", date(2035, 1, 1))

    assert parsed.value == date(2035, 3, 4)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("9:5", "09:05"), ("09:30", "09:30"), ("9 30", "09:30")],
)
def test_schedule_clock_uses_approved_separators(value: str, expected: str) -> None:
    assert parse_schedule_time(value) == expected


@pytest.mark.parametrize("value", ["09/30", "24:00", "09:60", "0900", "09  30"])
def test_schedule_clock_rejects_unapproved_or_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_schedule_time(value)


def test_combine_schedule_date_time_returns_naive_local_datetime() -> None:
    result = combine_schedule_date_time(date(2035, 3, 4), "9 30")

    assert result == datetime(2035, 3, 4, 9, 30)
    assert result.tzinfo is None


def test_human_datetime_helpers_keep_dates_compact_and_years_unambiguous() -> None:
    reference_date = date(2026, 12, 31)

    assert (
        format_human_datetime(datetime(2026, 11, 5, 9), reference_date=reference_date)
        == "November 5 at 9:00"
    )
    assert (
        format_human_datetime(datetime(2027, 11, 5, 9), reference_date=reference_date)
        == "November 5 at 9:00 (2027)"
    )
    assert (
        format_local_interval(
            datetime(2026, 11, 5, 15),
            datetime(2026, 11, 5, 16),
            reference_date=reference_date,
        )
        == "November 5 15:00 - 16:00"
    )
    assert (
        format_local_interval(
            datetime(2026, 12, 31, 23, 30),
            datetime(2027, 1, 1, 0, 30),
            reference_date=reference_date,
        )
        == "December 31 23:30 - January 1 (2027) 0:30"
    )
    assert (
        format_compact_schedule_date(date(2026, 11, 5), reference_date=reference_date)
        == "Thu, Nov 5"
    )
    assert (
        format_compact_schedule_date(date(2027, 11, 5), reference_date=reference_date)
        == "Fri, Nov 5 (2027)"
    )


def test_timezone_formatters_use_viewer_local_dates_and_clocks() -> None:
    reference_date = date(2026, 11, 5)
    start = datetime(2026, 11, 5, 20, tzinfo=UTC)
    end = datetime(2026, 11, 5, 21, tzinfo=UTC)

    assert (
        format_human_datetime_in_timezone(
            start, "America/New_York", reference_date=reference_date
        )
        == "November 5 at 15:00"
    )
    assert (
        format_local_interval_in_timezone(
            start, end, "Europe/London", reference_date=reference_date
        )
        == "November 5 20:00 - 21:00"
    )


def _resolved_interval(
    timezone_name: str, start_utc: datetime, end_utc: datetime
) -> LocalIntervalResolution:
    zone = ZoneInfo(timezone_name)

    def instant(value: datetime) -> ResolvedLocalInstant:
        local = value.astimezone(zone)
        offset = local.strftime("%z")
        return ResolvedLocalInstant(
            local=local,
            utc=value,
            utc_offset=f"{offset[:3]}:{offset[3:]}",
        )

    return LocalIntervalResolution(
        timezone=timezone_name,
        start=instant(start_utc),
        end=instant(end_utc),
    )


def test_local_interval_review_uses_viewer_timezone_and_labels_dst_fold() -> None:
    start = datetime(2024, 11, 3, 5, 30, tzinfo=UTC)
    end = datetime(2024, 11, 3, 6, 30, tzinfo=UTC)
    reference_date = date(2024, 1, 1)

    new_york = format_local_interval_review(
        _resolved_interval("America/New_York", start, end),
        reference_date=reference_date,
    )
    london = format_local_interval_review(
        _resolved_interval("Europe/London", start, end),
        reference_date=reference_date,
    )

    assert (
        new_york == "November 3 1:30 (first occurrence) - "
        "1:30 (second occurrence)\nTimezone: America/New_York"
    )
    assert "November 3 5:30 - 6:30" in london
    assert "UTC" not in new_york
    assert "2024-11-03" not in new_york


def test_local_interval_review_retains_duration_validation() -> None:
    reference_date = date(2024, 1, 1)
    interval = _resolved_interval(
        "UTC",
        datetime(2024, 1, 1, 10, tzinfo=UTC),
        datetime(2024, 1, 1, 11, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="must be positive"):
        format_local_interval_review(
            interval, reference_date=reference_date, duration_minutes=0
        )

    start = datetime.max.replace(tzinfo=UTC) - timedelta(minutes=1)
    overflow_interval = _resolved_interval(
        "UTC", start, datetime.max.replace(tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="outside the supported range"):
        format_local_interval_review(
            overflow_interval, reference_date=reference_date, duration_minutes=2
        )


async def test_backend_local_resolution_handles_dst_and_viewer_timezone(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher_id = 5000000020
    student_id = 5000000021
    teacher = await api.register(
        Registration(
            telegram_user_id=teacher_id,
            telegram_private_chat_id=teacher_id,
            full_name="New York Teacher",
            role="TEACHER",
            timezone="America/New_York",
        )
    )
    await api.register(
        Registration(
            telegram_user_id=student_id,
            telegram_private_chat_id=student_id,
            full_name="London Student",
            role="STUDENT",
            timezone="Europe/London",
            teacher_code=teacher.teacher.code,
        )
    )

    gap = await api.resolve_local_interval(
        teacher_id,
        teacher_id,
        datetime(2024, 3, 10, 2, 30),
        datetime(2024, 3, 10, 4, 0),
    )
    fold = await api.resolve_local_interval(
        teacher_id,
        teacher_id,
        datetime(2024, 11, 3, 1, 30),
        datetime(2024, 11, 3, 2, 30),
    )
    student_local = await api.resolve_local_interval(
        student_id,
        student_id,
        datetime(2024, 3, 10, 2, 30),
        datetime(2024, 3, 10, 4, 0),
    )

    assert gap.timezone == "America/New_York"
    assert gap.start.local.isoformat() == "2024-03-10T03:30:00-04:00"
    assert gap.start.utc == datetime(2024, 3, 10, 7, 30, tzinfo=UTC)
    assert gap.start.utc_offset == "-04:00"
    assert fold.start.local.isoformat() == "2024-11-03T01:30:00-04:00"
    assert fold.start.utc == datetime(2024, 11, 3, 5, 30, tzinfo=UTC)
    assert fold.start.utc_offset == "-04:00"
    assert student_local.timezone == "Europe/London"
    assert student_local.start.local.isoformat() == "2024-03-10T02:30:00+00:00"
    assert student_local.start.utc != gap.start.utc

    review = format_local_interval_review(gap, reference_date=date(2024, 1, 1))
    assert review == "March 10 3:30 - 4:00\nTimezone: America/New_York"
    assert "UTC" not in review
    assert "2024-03-10" not in review

    lesson_review = format_local_interval_review(
        fold, reference_date=date(2024, 1, 1), duration_minutes=60
    )
    assert lesson_review == (
        "November 3 1:30 (first occurrence) - "
        "1:30 (second occurrence)\nTimezone: America/New_York"
    )
    assert "UTC" not in lesson_review


async def test_local_resolution_rejects_aware_or_invalid_input_without_writing(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    user_id = 5000000022
    await api.register(
        Registration(
            telegram_user_id=user_id,
            telegram_private_chat_id=user_id,
            full_name="Resolver Teacher",
            role="TEACHER",
            timezone="UTC",
        )
    )

    with pytest.raises(ValidationError):
        await api.resolve_local_interval(
            user_id,
            user_id,
            datetime(2035, 1, 1, 10, tzinfo=UTC),
            datetime(2035, 1, 1, 11, tzinfo=UTC),
        )
    with pytest.raises(ValidationError):
        await api.resolve_local_interval(
            user_id,
            user_id,
            datetime(2035, 1, 1, 11),
            datetime(2035, 1, 1, 10),
        )
