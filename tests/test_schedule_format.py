from datetime import UTC, date, datetime, time, timedelta
from html import unescape
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from telegram.api import ScheduleDay, ScheduleItem, ScheduleResponse
from telegram.dialogs.schedule.formatting import (
    MAX_LABEL_CODEPOINTS,
    MAX_MESSAGE_UTF16_UNITS,
    format_schedule_pages,
)


def _item(
    source_id: int,
    start: str,
    end: str,
    *,
    status: str = "upcoming",
    event_type: str = "LESSON",
    subject_label: str | None = "Student",
) -> ScheduleItem:
    return ScheduleItem(
        source_kind="event",
        source_id=UUID(f"00000000-0000-0000-0000-{source_id:012d}"),
        event_type=event_type,
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        status=status,
        subject_label=subject_label,
    )


def _automatic_break(
    source_id: int,
    start: datetime,
    end: datetime,
    parent_start: datetime,
    *,
    parent_kind: str = "event",
    status: str = "upcoming",
) -> ScheduleItem:
    return ScheduleItem(
        source_kind="automatic_break",
        source_id=UUID(f"00000000-0000-0000-0000-{source_id:012d}"),
        event_type="BREAK",
        parent_kind=parent_kind,
        parent_start=parent_start,
        start=start,
        end=end,
        status=status,
    )


def _response(
    days: list[ScheduleDay],
    *,
    period: str = "day",
    timezone: str = "UTC",
    generated_at: str = "2026-10-19T12:00:00+00:00",
) -> ScheduleResponse:
    zone = ZoneInfo(timezone)
    range_start = datetime.combine(days[0].date, time.min, zone).astimezone(UTC)
    range_end = datetime.combine(
        days[-1].date + timedelta(days=1), time.min, zone
    ).astimezone(UTC)
    return ScheduleResponse(
        period=period,
        timezone=timezone,
        generated_at=datetime.fromisoformat(generated_at),
        range_start=range_start,
        range_end=range_end,
        days=days,
    )


def _visible_text(markup: str) -> str:
    return unescape(markup.replace("<b>", "").replace("</b>", ""))


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_week_includes_all_empty_days_and_snapshot_context() -> None:
    days = [ScheduleDay(date=date(2026, 10, day), items=[]) for day in range(19, 26)]
    response = _response(
        days,
        period="week",
        generated_at="2026-10-19T12:00:00+00:00",
    )
    pages = format_schedule_pages(response, "STUDENT")

    assert len(pages) == 1
    plain = _visible_text(pages[0])
    assert "This week's schedule" in plain
    assert "Updated Mon, Oct 19 at 12:00" in plain
    assert plain.count("No events") == 7
    assert all(
        f"{datetime(2026, 10, day):%a}" + f", Oct {day}" in plain
        for day in range(19, 26)
    )
    assert plain.endswith("Timezone: UTC")
    assert "Page " not in plain


def test_automatic_break_schedule_items_require_parent_reference() -> None:
    item = ScheduleItem(
        source_kind="automatic_break",
        source_id=UUID("00000000-0000-0000-0000-000000000001"),
        event_type="BREAK",
        parent_kind="event",
        parent_start=datetime.fromisoformat("2026-10-19T09:00:00+00:00"),
        start=datetime.fromisoformat("2026-10-19T10:00:00+00:00"),
        end=datetime.fromisoformat("2026-10-19T10:05:00+00:00"),
        status="upcoming",
    )
    assert item.parent_kind == "event"

    with pytest.raises(ValidationError):
        ScheduleItem(
            source_kind="automatic_break",
            source_id=UUID("00000000-0000-0000-0000-000000000001"),
            event_type="BREAK",
            start=datetime.fromisoformat("2026-10-19T10:00:00+00:00"),
            end=datetime.fromisoformat("2026-10-19T10:05:00+00:00"),
            status="upcoming",
        )
    with pytest.raises(ValidationError):
        ScheduleItem(
            source_kind="event",
            source_id=UUID("00000000-0000-0000-0000-000000000001"),
            event_type="BREAK",
            parent_kind="event",
            parent_start=datetime.fromisoformat("2026-10-19T09:00:00+00:00"),
            start=datetime.fromisoformat("2026-10-19T10:00:00+00:00"),
            end=datetime.fromisoformat("2026-10-19T10:05:00+00:00"),
            status="upcoming",
        )


def test_automatic_and_manual_breaks_have_distinct_read_only_labels() -> None:
    lesson = _item(
        1,
        "2026-10-19T02:00:00+00:00",
        "2026-10-19T03:00:00+00:00",
        status="past",
    )
    automatic_break = _automatic_break(
        1,
        datetime.fromisoformat("2026-10-19T03:00:00+00:00"),
        datetime.fromisoformat("2026-10-19T03:05:00+00:00"),
        datetime.fromisoformat("2026-10-19T02:00:00+00:00"),
    )
    manual_break = _item(
        2,
        "2026-10-19T03:05:00+00:00",
        "2026-10-19T04:05:00+00:00",
        event_type="BREAK",
        subject_label=None,
    )
    assert lesson.source_id == automatic_break.source_id
    assert lesson.source_kind != automatic_break.source_kind
    day = ScheduleDay(
        date=date(2026, 10, 19),
        items=[lesson, automatic_break, manual_break],
    )
    response = _response(
        [day],
        timezone="Asia/Bangkok",
        generated_at="2026-10-18T00:00:00+00:00",
    )

    teacher_text = _visible_text(format_schedule_pages(response, "TEACHER")[0])
    student_text = _visible_text(format_schedule_pages(response, "STUDENT")[0])

    assert "9:00–10:00  Lesson — Student  [PAST]" in teacher_text
    assert "10:00–10:05  Automatic break  ⏳" in teacher_text
    assert "10:05–11:05  Break  ⏳" in teacher_text
    assert "10:00–10:05  Teacher's automatic break  ⏳" in (student_text)
    assert "10:00–10:05  Automatic break" not in student_text


def test_automatic_break_local_times_disambiguate_dst_fold() -> None:
    day = ScheduleDay(
        date=date(2026, 11, 1),
        items=[
            _automatic_break(
                1,
                datetime.fromisoformat("2026-11-01T05:30:00+00:00"),
                datetime.fromisoformat("2026-11-01T05:45:00+00:00"),
                datetime.fromisoformat("2026-11-01T04:30:00+00:00"),
            ),
            _automatic_break(
                2,
                datetime.fromisoformat("2026-11-01T06:30:00+00:00"),
                datetime.fromisoformat("2026-11-01T06:45:00+00:00"),
                datetime.fromisoformat("2026-11-01T05:30:00+00:00"),
                parent_kind="recurrent_event",
            ),
        ],
    )
    response = _response(
        [day],
        timezone="America/New_York",
        generated_at="2026-11-01T04:00:00+00:00",
    )

    text = _visible_text(format_schedule_pages(response, "TEACHER")[0])

    assert "1:30 (first occurrence)" in text
    assert "1:30 (second occurrence)" in text
    assert text.count("Automatic break") == 2


def test_automatic_breaks_paginate_without_omitting_rows() -> None:
    items = []
    for index in range(160):
        start = datetime(2026, 10, 19, 8, tzinfo=UTC) + timedelta(minutes=index * 5)
        items.append(
            _automatic_break(
                index + 1,
                start,
                start + timedelta(minutes=3),
                start - timedelta(hours=1),
            )
        )
    days = [ScheduleDay(date=date(2026, 10, 19), items=items)] + [
        ScheduleDay(date=date(2026, 10, day), items=[]) for day in range(20, 26)
    ]
    response = _response(
        days,
        period="week",
        generated_at="2026-10-18T00:00:00+00:00",
    )

    pages = format_schedule_pages(response, "STUDENT")
    visible_pages = [_visible_text(page) for page in pages]
    all_text = "\n".join(visible_pages)

    assert len(pages) > 1
    assert all(_utf16_length(page) <= MAX_MESSAGE_UTF16_UNITS for page in visible_pages)
    assert all(page.endswith("Timezone: UTC") for page in visible_pages)
    assert all_text.count("Teacher's automatic break") == 160
    assert "[UPCOMING]" not in all_text
    assert "(read-only)" not in all_text
    assert "Page " not in all_text


def test_teacher_names_are_html_escaped_but_student_labels_are_suppressed() -> None:
    day = ScheduleDay(
        date=date(2026, 10, 19),
        items=[
            _item(
                1,
                "2026-10-19T09:00:00+00:00",
                "2026-10-19T10:00:00+00:00",
                subject_label="A <B & C>",
            )
        ],
    )
    response = _response([day])

    teacher_text = format_schedule_pages(response, "TEACHER")[0]
    student_text = format_schedule_pages(response, "STUDENT")[0]

    assert "A &lt;B &amp; C&gt;" in teacher_text
    assert "A <B & C>" not in teacher_text
    assert "A <B & C>" not in student_text
    assert "Lesson" in _visible_text(student_text)


def test_now_divider_is_only_on_current_date_and_between_status_groups() -> None:
    days = [
        ScheduleDay(
            date=date(2026, 10, 19),
            items=[
                _item(
                    1,
                    "2026-10-19T08:00:00+00:00",
                    "2026-10-19T09:00:00+00:00",
                    status="past",
                ),
                _item(
                    2,
                    "2026-10-19T11:00:00+00:00",
                    "2026-10-19T13:00:00+00:00",
                    status="ongoing",
                ),
                _item(
                    3,
                    "2026-10-19T14:00:00+00:00",
                    "2026-10-19T15:00:00+00:00",
                    status="upcoming",
                ),
                _automatic_break(
                    4,
                    datetime.fromisoformat("2026-10-19T15:00:00+00:00"),
                    datetime.fromisoformat("2026-10-19T15:05:00+00:00"),
                    datetime.fromisoformat("2026-10-19T14:00:00+00:00"),
                ),
            ],
        ),
        ScheduleDay(date=date(2026, 10, 20), items=[]),
        *[ScheduleDay(date=date(2026, 10, day), items=[]) for day in range(21, 26)],
    ]
    response = _response(
        days,
        period="week",
        generated_at="2026-10-19T12:00:00+00:00",
    )

    text = "\n".join(format_schedule_pages(response, "STUDENT"))
    plain = _visible_text(text)

    assert plain.count("NOW") == 1
    assert plain.index("[PAST]") < plain.index("NOW")
    assert plain.index("NOW") < plain.index("[ONGOING]")
    assert plain.index("[ONGOING]") < plain.index("⏳")
    assert plain.index("⏳") < plain.index("Teacher's automatic break")


def test_current_empty_day_omits_now_and_timezone_footer() -> None:
    response = _response(
        [ScheduleDay(date=date(2026, 10, 19), items=[])],
        generated_at="2026-10-19T12:00:00+00:00",
    )

    text = _visible_text(format_schedule_pages(response, "STUDENT")[0])

    assert "No events" in text
    assert "Today's schedule" in text
    assert "Mon, Oct 19" in text
    assert "NOW" not in text
    assert "Timezone:" not in text


def test_repeated_hour_times_include_fold_label_without_zone_offset() -> None:
    response = _response(
        [
            ScheduleDay(
                date=date(2026, 11, 1),
                items=[
                    _item(
                        1,
                        "2026-11-01T05:30:00+00:00",
                        "2026-11-01T05:45:00+00:00",
                        subject_label=None,
                    ),
                    _item(
                        2,
                        "2026-11-01T06:30:00+00:00",
                        "2026-11-01T06:45:00+00:00",
                        subject_label=None,
                    ),
                ],
            )
        ],
        timezone="America/New_York",
        generated_at="2026-11-01T12:00:00+00:00",
    )

    text = _visible_text(format_schedule_pages(response, "STUDENT")[0])

    assert "1:30 (first occurrence)" in text
    assert "1:30 (second occurrence)" in text
    assert "UTC-04:00" not in text
    assert "UTC-05:00" not in text


def test_cross_midnight_item_is_rendered_on_start_date_with_full_interval() -> None:
    response = _response(
        [
            ScheduleDay(
                date=date(2026, 10, 19),
                items=[
                    _item(
                        1,
                        "2026-10-19T16:30:00+00:00",
                        "2026-10-19T18:30:00+00:00",
                        subject_label=None,
                    )
                ],
            ),
            ScheduleDay(date=date(2026, 10, 20), items=[]),
            *[ScheduleDay(date=date(2026, 10, day), items=[]) for day in range(21, 26)],
        ],
        period="week",
        timezone="Asia/Bangkok",
        generated_at="2026-10-18T00:00:00+00:00",
    )

    text = _visible_text("\n".join(format_schedule_pages(response, "STUDENT")))

    assert "Mon, Oct 19 23:30–Tue, Oct 20 1:30" in text
    assert text.count("Mon, Oct 19 23:30–Tue, Oct 20 1:30") == 1


def test_cross_midnight_interval_includes_year_at_year_boundary() -> None:
    response = _response(
        [
            ScheduleDay(
                date=date(2026, 12, 31),
                items=[
                    _item(
                        1,
                        "2026-12-31T23:00:00+00:00",
                        "2027-01-01T01:00:00+00:00",
                        subject_label=None,
                    )
                ],
            )
        ],
        generated_at="2026-12-31T12:00:00+00:00",
    )

    text = _visible_text(format_schedule_pages(response, "STUDENT")[0])

    assert "Thu, Dec 31 23:00–Fri, Jan 1 (2027) 1:00" in text


def test_oversized_week_paginates_complete_rows_with_safe_labels_and_budget() -> None:
    items = [
        _item(
            index + 1,
            f"2026-10-19T{index // 60:02d}:{index % 60:02d}:00+00:00",
            f"2026-10-19T{index // 60:02d}:{index % 60:02d}:30+00:00",
            subject_label=f"Student {index:03d} 😀 <&>" + "x" * 100,
        )
        for index in range(160)
    ]
    days = [ScheduleDay(date=date(2026, 10, 19), items=items)] + [
        ScheduleDay(date=date(2026, 10, day), items=[]) for day in range(20, 26)
    ]
    response = _response(
        days,
        period="week",
        generated_at="2026-10-18T00:00:00+00:00",
    )

    pages = format_schedule_pages(response, "TEACHER")
    visible_pages = [_visible_text(page) for page in pages]

    assert len(pages) > 1
    assert all(_utf16_length(page) <= MAX_MESSAGE_UTF16_UNITS for page in visible_pages)
    assert all(page.endswith("Timezone: UTC") for page in visible_pages)
    assert all("Page " not in page for page in visible_pages)
    assert (
        sum(
            line.count("Lesson — Student")
            for page in visible_pages
            for line in page.splitlines()
        )
        == 160
    )
    assert all("[UPCOMING]" not in page for page in visible_pages)
    assert all("(read-only)" not in page for page in visible_pages)
    assert all(
        f"Student {index:03d}" in "\n".join(visible_pages) for index in range(160)
    )
    rendered_label = (
        next(label for label in visible_pages[0].splitlines() if "Student 000" in label)
        .split(" — ", maxsplit=1)[1]
        .rsplit("  ⏳", maxsplit=1)[0]
    )
    assert len(rendered_label) == MAX_LABEL_CODEPOINTS
    assert "\n".join(visible_pages).count("No events") == 6
