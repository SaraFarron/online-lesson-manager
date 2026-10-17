from datetime import UTC, date, datetime, time, timedelta
from html import unescape
from uuid import UUID
from zoneinfo import ZoneInfo

from telegram.api import ScheduleDay, ScheduleItem, ScheduleResponse
from telegram.schedule_format import (
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
    assert "Updated at 2026-10-19 12:00 (UTC)" in plain
    assert plain.count("No events") == 7
    assert all(
        datetime(2026, 10, day).strftime("%A, %B %d, %Y") in plain
        for day in range(19, 26)
    )


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
    assert plain.index("[ONGOING]") < plain.index("[UPCOMING]")


def test_current_empty_day_keeps_empty_state_and_now_divider() -> None:
    response = _response(
        [ScheduleDay(date=date(2026, 10, 19), items=[])],
        generated_at="2026-10-19T12:00:00+00:00",
    )

    text = _visible_text(format_schedule_pages(response, "STUDENT")[0])

    assert "No events" in text
    assert "NOW" in text


def test_repeated_hour_times_include_distinguishing_zone_and_offset() -> None:
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

    assert "01:30 EDT (UTC-04:00)" in text
    assert "01:30 EST (UTC-05:00)" in text


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

    assert "23:30–01:30 (+1 day)" in text
    assert text.count("23:30–01:30 (+1 day)") == 1


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
    assert all(
        "Page " + str(index) + "/" + str(len(pages)) in page
        for index, page in enumerate(visible_pages, start=1)
    )
    assert all(_utf16_length(page) <= MAX_MESSAGE_UTF16_UNITS for page in visible_pages)
    assert sum(page.count("[UPCOMING]") for page in visible_pages) == 160
    assert all(
        f"Student {index:03d}" in "\n".join(visible_pages) for index in range(160)
    )
    rendered_label = (
        next(label for label in visible_pages[0].splitlines() if "Student 000" in label)
        .split(" — ", maxsplit=1)[1]
        .rsplit("  [", maxsplit=1)[0]
    )
    assert len(rendered_label) == MAX_LABEL_CODEPOINTS
    assert "\n".join(visible_pages).count("No events") == 6
