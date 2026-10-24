from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from telegram.api import BackendClient, Registration
from telegram.inputs import (
    combine_schedule_date_time,
    format_local_interval_review,
    format_schedule_date_confirmation,
    parse_schedule_date,
    parse_schedule_time,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("03 04", date(2035, 3, 4)),
        ("03:04", date(2035, 3, 4)),
        ("03/04", date(2035, 3, 4)),
        ("03 04 2036", date(2036, 3, 4)),
        ("03:04:2036", date(2036, 3, 4)),
        ("03/04/2036", date(2036, 3, 4)),
    ],
)
def test_schedule_date_grammar(value: str, expected: date) -> None:
    parsed = parse_schedule_date(value, date(2035, 1, 1))
    assert parsed.value == expected
    assert not parsed.requires_confirmation


def test_schedule_date_infers_next_year_and_requires_confirmation() -> None:
    parsed = parse_schedule_date("01/02", date(2035, 12, 30))

    assert parsed.value == date(2036, 1, 2)
    assert parsed.inferred_year
    assert parsed.requires_confirmation
    assert "2036-01-02" in format_schedule_date_confirmation(parsed)


def test_explicit_past_date_is_preserved_and_leap_days_are_checked() -> None:
    past = parse_schedule_date("03/04/2034", date(2035, 3, 5))
    leap_day = parse_schedule_date("02/29/2024", date(2025, 1, 1))
    upcoming_leap_day = parse_schedule_date("02/29", date(2024, 2, 28))

    assert past.value == date(2034, 3, 4)
    assert leap_day.value == date(2024, 2, 29)
    assert upcoming_leap_day.value == date(2024, 2, 29)
    assert not past.requires_confirmation


@pytest.mark.parametrize(
    ("value", "today", "message"),
    [
        ("02/30", date(2035, 1, 1), "valid calendar date"),
        ("13/01", date(2035, 1, 1), "valid calendar date"),
        ("02/29", date(2025, 3, 1), "valid calendar date"),
        ("02/29", date(2024, 3, 1), "explicit year"),
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

    review = format_local_interval_review(gap)
    assert "Timezone: America/New_York" in review
    assert "Start: 2024-03-10 03:30 (UTC-04:00)" in review
    assert "UTC interval:" in review


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
