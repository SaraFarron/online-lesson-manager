from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.occurrences import (
    Occurrence,
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    derive_automatic_break,
    expand_recurrence,
)
from backend.exceptions import ValidationError


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _series(
    start: str,
    end: str,
    interval_end: str | None,
    *,
    interval: int = 7,
    mode: RecurrenceMode = RecurrenceMode.UTC_STEP,
    timezone: str | None = None,
) -> RecurrenceSeries:
    return RecurrenceSeries(
        source=OccurrenceSource(
            kind=OccurrenceSourceKind.RECURRENT_EVENT,
            id=UUID(int=1),
            teacher_id=UUID(int=2),
            user_id=UUID(int=3),
            event_type=EventType.LESSON,
        ),
        start=_datetime(start),
        end=_datetime(end),
        interval=interval,
        interval_end=_datetime(interval_end) if interval_end is not None else None,
        recurrence_mode=mode,
        recurrence_timezone=timezone,
    )


def test_utc_step_series_expands_weekly_and_keeps_stable_identity() -> None:
    series = _series(
        "2026-01-01T09:00:00",
        "2026-01-01T10:00:00",
        "2026-02-01T09:00:00",
    )

    occurrences = expand_recurrence(
        series,
        _datetime("2026-01-15T09:00:00"),
        _datetime("2026-01-29T09:00:00"),
    )

    assert [occurrence.start for occurrence in occurrences] == [
        _datetime("2026-01-15T09:00:00+00:00"),
        _datetime("2026-01-22T09:00:00+00:00"),
    ]
    assert all(occurrence.start.tzinfo is UTC for occurrence in occurrences)
    assert occurrences[0].identity == (
        OccurrenceSourceKind.RECURRENT_EVENT,
        series.source.id,
        occurrences[0].start,
    )


def test_occurrence_crossing_query_lower_bound_is_returned_unclipped() -> None:
    series = _series(
        "2026-10-01T23:00:00+00:00",
        "2026-10-02T01:00:00+00:00",
        "2026-10-30T23:00:00+00:00",
    )

    occurrences = expand_recurrence(
        series,
        _datetime("2026-10-02T00:30:00+00:00"),
        _datetime("2026-10-02T01:00:00+00:00"),
    )

    assert len(occurrences) == 1
    assert occurrences[0].start == _datetime("2026-10-01T23:00:00+00:00")
    assert occurrences[0].end == _datetime("2026-10-02T01:00:00+00:00")
    assert (
        expand_recurrence(
            series,
            _datetime("2026-10-02T01:00:00+00:00"),
            _datetime("2026-10-03T00:00:00+00:00"),
        )
        == []
    )


def test_multi_day_occurrence_is_returned_without_query_clipping() -> None:
    series = _series(
        "2026-10-01T23:00:00+00:00",
        "2026-10-03T01:00:00+00:00",
        "2026-10-30T23:00:00+00:00",
    )

    occurrences = expand_recurrence(
        series,
        _datetime("2026-10-02T00:30:00+00:00"),
        _datetime("2026-10-02T01:00:00+00:00"),
    )

    assert len(occurrences) == 1
    assert occurrences[0].start == _datetime("2026-10-01T23:00:00+00:00")
    assert occurrences[0].end == _datetime("2026-10-03T01:00:00+00:00")


def test_occurrence_start_at_exclusive_cutoff_is_not_generated() -> None:
    series = _series(
        "2026-10-01T09:00:00+00:00",
        "2026-10-01T10:00:00+00:00",
        "2026-10-08T09:00:00+00:00",
    )

    occurrences = expand_recurrence(
        series,
        _datetime("2026-10-01T00:00:00+00:00"),
        _datetime("2026-10-15T00:00:00+00:00"),
    )

    assert [occurrence.start for occurrence in occurrences] == [
        _datetime("2026-10-01T09:00:00+00:00")
    ]


@pytest.mark.parametrize(
    ("anchor_start", "anchor_end", "cutoff", "query_start", "query_end", "expected"),
    [
        (
            "2026-03-22T01:30:00+00:00",
            "2026-03-22T02:30:00+00:00",
            "2026-04-19T01:30:00+00:00",
            "2026-03-29T00:00:00+00:00",
            "2026-03-29T03:00:00+00:00",
            ("2026-03-29T01:30:00+00:00", "2026-03-29T02:30:00+00:00"),
        ),
        (
            "2026-10-18T00:30:00+00:00",
            "2026-10-18T01:30:00+00:00",
            "2026-11-15T01:30:00+00:00",
            "2026-10-25T00:00:00+00:00",
            "2026-10-25T02:00:00+00:00",
            ("2026-10-25T00:30:00+00:00", "2026-10-25T01:30:00+00:00"),
        ),
    ],
)
def test_teacher_local_series_observes_dst_gap_and_fold_policy(
    anchor_start: str,
    anchor_end: str,
    cutoff: str,
    query_start: str,
    query_end: str,
    expected: tuple[str, str],
) -> None:
    series = _series(
        anchor_start,
        anchor_end,
        cutoff,
        mode=RecurrenceMode.TEACHER_LOCAL,
        timezone="Europe/Berlin",
    )

    occurrences = expand_recurrence(
        series, _datetime(query_start), _datetime(query_end)
    )

    assert len(occurrences) == 1
    assert (occurrences[0].start, occurrences[0].end) == tuple(
        _datetime(value) for value in expected
    )


def test_coincident_local_starts_collapse_to_one_utc_occurrence() -> None:
    series = _series(
        "2011-12-29T19:00:00+00:00",
        "2011-12-29T20:00:00+00:00",
        "2012-01-05T19:00:00+00:00",
        interval=1,
        mode=RecurrenceMode.TEACHER_LOCAL,
        timezone="Pacific/Apia",
    )

    occurrences = expand_recurrence(
        series,
        _datetime("2011-12-29T18:00:00+00:00"),
        _datetime("2011-12-31T00:00:00+00:00"),
    )

    assert [occurrence.start for occurrence in occurrences] == [
        _datetime("2011-12-29T19:00:00+00:00"),
        _datetime("2011-12-30T19:00:00+00:00"),
    ]
    assert len({occurrence.identity for occurrence in occurrences}) == 2


def test_adjacent_queries_return_the_same_shared_occurrence_identity() -> None:
    series = _series(
        "2026-10-01T09:00:00+00:00",
        "2026-10-01T10:00:00+00:00",
        "2026-11-01T09:00:00+00:00",
        interval=2,
    )

    left = expand_recurrence(
        series,
        _datetime("2026-10-05T00:00:00+00:00"),
        _datetime("2026-10-09T00:00:00+00:00"),
    )
    right = expand_recurrence(
        series,
        _datetime("2026-10-07T00:00:00+00:00"),
        _datetime("2026-10-11T00:00:00+00:00"),
    )

    left_by_identity = {occurrence.identity: occurrence for occurrence in left}
    right_by_identity = {occurrence.identity: occurrence for occurrence in right}
    shared_identities = left_by_identity.keys() & right_by_identity.keys()
    assert len(shared_identities) == 1
    identity = next(iter(shared_identities))
    left_occurrence = left_by_identity[identity]
    right_occurrence = right_by_identity[identity]
    assert left_occurrence.start == right_occurrence.start
    assert left_occurrence.end == right_occurrence.end


@pytest.mark.parametrize(
    "kind",
    [OccurrenceSourceKind.EVENT, OccurrenceSourceKind.RECURRENT_EVENT],
)
def test_derived_break_identity_tracks_parent_occurrence(
    kind: OccurrenceSourceKind,
) -> None:
    parent_start = _datetime("2026-10-25T00:30:00+00:00")
    lesson = Occurrence(
        source=OccurrenceSource(
            kind=kind,
            id=UUID(int=7),
            teacher_id=UUID(int=8),
            user_id=UUID(int=9),
            event_type=EventType.LESSON,
            post_break_minutes=30,
        ),
        start=parent_start,
        end=_datetime("2026-10-25T01:30:00+00:00"),
    )

    automatic_break = derive_automatic_break(lesson)

    assert automatic_break is not None
    assert (automatic_break.start, automatic_break.end) == (
        lesson.end,
        lesson.end + timedelta(minutes=30),
    )
    assert automatic_break.source.kind == OccurrenceSourceKind.AUTOMATIC_BREAK
    assert automatic_break.source.parent_kind == kind
    assert automatic_break.identity == (
        OccurrenceSourceKind.AUTOMATIC_BREAK,
        lesson.source.id,
        lesson.start,
    )


def test_zero_duration_does_not_create_a_derived_break() -> None:
    lesson = Occurrence(
        source=OccurrenceSource(
            kind=OccurrenceSourceKind.EVENT,
            id=UUID(int=7),
            teacher_id=UUID(int=8),
            user_id=UUID(int=9),
            event_type=EventType.LESSON,
        ),
        start=_datetime("2026-10-25T00:30:00+00:00"),
        end=_datetime("2026-10-25T01:30:00+00:00"),
    )

    assert derive_automatic_break(lesson) is None


def test_derived_break_uses_elapsed_utc_minutes_across_dst_fold() -> None:
    lesson = Occurrence(
        source=OccurrenceSource(
            kind=OccurrenceSourceKind.EVENT,
            id=UUID(int=7),
            teacher_id=UUID(int=8),
            user_id=UUID(int=9),
            event_type=EventType.LESSON,
            post_break_minutes=30,
        ),
        start=_datetime("2026-10-25T00:30:00+00:00"),
        end=_datetime("2026-10-25T01:30:00+00:00"),
    )

    automatic_break = derive_automatic_break(lesson)

    assert automatic_break is not None
    assert automatic_break.end == _datetime("2026-10-25T02:00:00+00:00")


def test_derived_break_reports_datetime_overflow() -> None:
    maximum = datetime.max.replace(tzinfo=UTC)
    lesson = Occurrence(
        source=OccurrenceSource(
            kind=OccurrenceSourceKind.EVENT,
            id=UUID(int=7),
            teacher_id=UUID(int=8),
            user_id=UUID(int=9),
            event_type=EventType.LESSON,
            post_break_minutes=1,
        ),
        start=maximum - timedelta(minutes=1),
        end=maximum,
    )

    with pytest.raises(ValidationError, match="outside the supported datetime range"):
        derive_automatic_break(lesson)


@pytest.mark.parametrize(
    ("mode", "timezone"),
    [
        (RecurrenceMode.UTC_STEP, None),
        (RecurrenceMode.TEACHER_LOCAL, "Europe/Berlin"),
    ],
)
def test_later_query_seeks_instead_of_walking_all_prior_daily_occurrences(
    mode: RecurrenceMode, timezone: str | None
) -> None:
    series = _series(
        "2000-01-01T09:00:00+00:00",
        "2000-01-01T10:00:00+00:00",
        "2040-01-01T09:00:00+00:00",
        interval=1,
        mode=mode,
        timezone=timezone,
    )

    occurrences = expand_recurrence(
        series,
        _datetime("2039-12-01T00:00:00+00:00"),
        _datetime("2039-12-03T00:00:00+00:00"),
        max_occurrences=20,
    )

    assert [occurrence.start for occurrence in occurrences] == [
        _datetime("2039-12-01T09:00:00+00:00"),
        _datetime("2039-12-02T09:00:00+00:00"),
    ]


@pytest.mark.parametrize(
    ("mode", "timezone"),
    [
        (RecurrenceMode.UTC_STEP, None),
        (RecurrenceMode.TEACHER_LOCAL, "Europe/Berlin"),
    ],
)
def test_open_ended_series_seeks_to_distant_bounded_queries(
    mode: RecurrenceMode, timezone: str | None
) -> None:
    anchor = _datetime("2000-01-01T09:00:00+00:00")
    series = _series(
        anchor.isoformat(),
        (anchor + timedelta(hours=1)).isoformat(),
        None,
        mode=mode,
        timezone=timezone,
    )
    distant_start = anchor + timedelta(weeks=2087)

    occurrences = expand_recurrence(
        series,
        distant_start,
        distant_start + timedelta(days=14),
        max_occurrences=10,
    )

    assert [occurrence.start for occurrence in occurrences] == [
        distant_start,
        distant_start + timedelta(days=7),
    ]


def test_expansion_fails_instead_of_returning_a_partial_result_at_budget() -> None:
    series = _series(
        "2026-01-01T09:00:00+00:00",
        "2026-01-01T10:00:00+00:00",
        "2026-02-01T09:00:00+00:00",
        interval=1,
    )

    with pytest.raises(ValidationError, match="exceeds the limit"):
        expand_recurrence(
            series,
            _datetime("2026-01-01T00:00:00+00:00"),
            _datetime("2026-01-10T00:00:00+00:00"),
            max_occurrences=2,
        )


def test_expansion_limit_cannot_exceed_the_approved_global_budget() -> None:
    series = _series(
        "2026-01-01T09:00:00+00:00",
        "2026-01-01T10:00:00+00:00",
        "2026-02-01T09:00:00+00:00",
    )

    with pytest.raises(ValidationError, match="cannot exceed"):
        expand_recurrence(
            series,
            _datetime("2026-01-01T00:00:00+00:00"),
            _datetime("2026-01-02T00:00:00+00:00"),
            max_occurrences=10_001,
        )


@pytest.mark.parametrize(
    ("series", "range_start", "range_end"),
    [
        (
            _series(
                "2026-01-01T09:00:00+00:00",
                "2026-01-01T10:00:00+00:00",
                "2026-02-01T09:00:00+00:00",
            ),
            "2026-01-02T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
        (
            replace(
                _series(
                    "2026-01-01T09:00:00+00:00",
                    "2026-01-01T10:00:00+00:00",
                    "2026-02-01T09:00:00+00:00",
                ),
                recurrence_mode="INVALID",
            ),
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ),
        (
            replace(
                _series(
                    "2026-01-01T09:00:00+00:00",
                    "2026-01-01T10:00:00+00:00",
                    "2026-02-01T09:00:00+00:00",
                ),
                recurrence_timezone="UTC",
            ),
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ),
        (
            replace(
                _series(
                    "2026-01-01T09:00:00+00:00",
                    "2026-01-01T10:00:00+00:00",
                    "2026-02-01T09:00:00+00:00",
                ),
                recurrence_mode=RecurrenceMode.TEACHER_LOCAL,
            ),
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ),
        (
            replace(
                _series(
                    "2026-01-01T09:00:00+00:00",
                    "2026-01-01T10:00:00+00:00",
                    "2026-02-01T09:00:00+00:00",
                ),
                interval=10**12,
            ),
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ),
        (
            replace(
                _series(
                    "2026-01-01T09:00:00+00:00",
                    "2026-01-01T10:00:00+00:00",
                    "2026-02-01T09:00:00+00:00",
                ),
                interval=0,
            ),
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ),
    ],
)
def test_invalid_series_or_query_ranges_raise(
    series: RecurrenceSeries, range_start: str, range_end: str
) -> None:
    with pytest.raises(ValidationError):
        expand_recurrence(series, _datetime(range_start), _datetime(range_end))
