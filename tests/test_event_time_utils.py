from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.events.time_utils import (
    from_storage_utc,
    intervals_overlap,
    local_day_bounds,
    local_week_bounds,
    normalize_utc,
    resolve_local_datetime,
    to_storage_utc,
)
from backend.exceptions import ValidationError


def test_normalize_utc_treats_naive_values_as_utc_and_converts_offsets() -> None:
    naive = datetime(2026, 10, 15, 9)
    offset = datetime.fromisoformat("2026-10-15T16:00:00+07:00")

    assert normalize_utc(naive) == datetime(2026, 10, 15, 9, tzinfo=UTC)
    assert normalize_utc(offset) == datetime(2026, 10, 15, 9, tzinfo=UTC)


def test_storage_conversion_round_trips_naive_utc_values() -> None:
    aware = datetime.fromisoformat("2026-10-15T16:00:00+07:00")

    stored = to_storage_utc(aware)

    assert stored == datetime(2026, 10, 15, 9)
    assert stored.tzinfo is None
    assert from_storage_utc(stored) == datetime(2026, 10, 15, 9, tzinfo=UTC)


def test_local_day_bounds_account_for_dst_transitions() -> None:
    spring_start, spring_end = local_day_bounds(date(2026, 3, 29), "Europe/Berlin")
    fall_start, fall_end = local_day_bounds(date(2026, 10, 25), "Europe/Berlin")

    assert spring_start == datetime.fromisoformat("2026-03-28T23:00:00+00:00")
    assert spring_end == datetime.fromisoformat("2026-03-29T22:00:00+00:00")
    assert spring_end - spring_start == timedelta(hours=23)
    assert fall_start == datetime.fromisoformat("2026-10-24T22:00:00+00:00")
    assert fall_end == datetime.fromisoformat("2026-10-25T23:00:00+00:00")
    assert fall_end - fall_start == timedelta(hours=25)


def test_local_week_bounds_start_on_monday_in_viewer_timezone() -> None:
    start, end = local_week_bounds(date(2026, 3, 29), "Europe/Berlin")

    assert start == datetime.fromisoformat("2026-03-22T23:00:00+00:00")
    assert end == datetime.fromisoformat("2026-03-29T22:00:00+00:00")
    assert end - start == timedelta(days=7) - timedelta(hours=1)


def test_resolve_local_datetime_shifts_gap_forward_by_gap() -> None:
    resolved = resolve_local_datetime(datetime(2026, 3, 29, 2, 30), "Europe/Berlin")

    assert resolved.isoformat() == "2026-03-29T03:30:00+02:00"
    assert normalize_utc(resolved) == datetime.fromisoformat(
        "2026-03-29T01:30:00+00:00"
    )


def test_resolve_local_datetime_selects_earlier_fold_instant() -> None:
    resolved = resolve_local_datetime(datetime(2026, 10, 25, 2, 30), "Europe/Berlin")

    assert resolved.isoformat() == "2026-10-25T02:30:00+02:00"
    assert normalize_utc(resolved) == datetime.fromisoformat(
        "2026-10-25T00:30:00+00:00"
    )


def test_explicit_offset_fold_is_not_reinterpreted_as_local_wall_time() -> None:
    explicit_later_fold = datetime.fromisoformat("2026-10-25T02:30:00+01:00")

    assert normalize_utc(explicit_later_fold) == datetime.fromisoformat(
        "2026-10-25T01:30:00+00:00"
    )


def test_intervals_use_half_open_overlap_semantics() -> None:
    start = datetime.fromisoformat("2026-10-15T09:00:00+00:00")
    boundary = datetime.fromisoformat("2026-10-15T10:00:00+00:00")
    end = datetime.fromisoformat("2026-10-15T11:00:00+00:00")

    assert not intervals_overlap(start, boundary, boundary, end)
    assert intervals_overlap(start, boundary, boundary - timedelta(minutes=1), end)


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        (resolve_local_datetime, (datetime(2026, 1, 1), "Not/AZone")),
        (
            resolve_local_datetime,
            (datetime.fromisoformat("2026-01-01T09:00:00+00:00"), "UTC"),
        ),
        (
            intervals_overlap,
            (
                datetime(2026, 1, 1, 10),
                datetime(2026, 1, 1, 9),
                datetime(2026, 1, 1, 11),
                datetime(2026, 1, 1, 12),
            ),
        ),
    ],
)
def test_invalid_temporal_inputs_raise_validation_error(
    operation: Callable[..., object], args: tuple[object, ...]
) -> None:
    with pytest.raises(ValidationError):
        operation(*args)
