from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import escape
from typing import Literal
from unicodedata import category
from zoneinfo import ZoneInfo

from telegram.api import ScheduleDay, ScheduleItem, ScheduleResponse
from telegram.shared.inputs import format_compact_schedule_date, format_human_time

ViewerRole = Literal["TEACHER", "STUDENT"]
MAX_MESSAGE_UTF16_UNITS = 3500
MAX_LABEL_CODEPOINTS = 80
STALE_SNAPSHOT_AFTER = timedelta(hours=1)

_EVENT_LABELS = {
    "LESSON": "Lesson",
    "BREAK": "Break",
    "VACATION": "Vacation",
}
_STATUS_MARKERS = {"past": "✅", "ongoing": "▶️", "upcoming": "⏳"}
_AUTOMATIC_BREAK_LABELS: dict[ViewerRole, str] = {
    "TEACHER": "Automatic break",
    "STUDENT": "Teacher's automatic break",
}


@dataclass(frozen=True, slots=True)
class _TextLine:
    markup: str
    plain: str


def format_schedule_pages(
    response: ScheduleResponse,
    role: ViewerRole,
    *,
    source: Literal["live", "cached"] = "live",
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Format a typed backend snapshot into complete, Telegram-safe HTML pages."""
    zone = ZoneInfo(response.timezone)
    current_date = response.generated_at.astimezone(zone).date()
    updated_at = response.generated_at.astimezone(zone)
    updated_date = format_compact_schedule_date(
        current_date, reference_date=current_date
    )
    updated_time = format_human_time(updated_at)
    header_lines: tuple[_TextLine, ...] = ()
    if source == "cached":
        range_start = format_compact_schedule_date(
            response.days[0].date, reference_date=current_date
        )
        range_title = (
            f"Schedule for {range_start}"
            if response.period == "day"
            else f"Week starting {range_start}"
        )
        snapshot_status = (
            f"Snapshot as of {updated_date} at {updated_time} "
            f"({escape(response.timezone)}); event statuses apply as of this time"
        )
        header_lines = (
            _TextLine(
                "<b>OFFLINE — cached schedule</b>",
                "OFFLINE — cached schedule",
            ),
            _TextLine(f"<b>{escape(range_title)}</b>", range_title),
            _TextLine(snapshot_status, snapshot_status),
        )
    header_markup = "\n".join(line.markup for line in header_lines)
    header_plain = "\n".join(line.plain for line in header_lines)
    empty_today = response.period == "day" and not any(
        day.items for day in response.days
    )
    footer_lines: list[_TextLine] = []
    if not empty_today:
        footer_lines.append(
            _TextLine(
                f"Timezone: {escape(response.timezone)}",
                f"Timezone: {response.timezone}",
            )
        )
    if source == "live" and _is_stale(response.generated_at, now):
        footer_lines.append(
            _TextLine(
                f"Updated {updated_date} at {escape(updated_time)}",
                f"Updated {updated_date} at {updated_time}",
            )
        )
    footer = tuple(footer_lines)
    # Live pages separate the footer with a blank line; cached pages keep one newline.
    footer_gap = "\n\n" if source == "live" else "\n"
    header_length = _utf16_length(header_plain) + 1 if header_lines else 0
    footer_length = (
        len(footer_gap) + _utf16_length(_lines_plain(footer)) if footer else 0
    )
    body_limit = MAX_MESSAGE_UTF16_UNITS - header_length - footer_length
    if body_limit <= 0:
        raise ValueError("Schedule header exceeds the Telegram message budget")

    chunks: list[tuple[_TextLine, ...]] = []
    for day in response.days:
        chunks.extend(
            _day_chunks(
                day,
                role,
                zone,
                current_date,
                body_limit,
                suppress_empty_today_now=empty_today,
                show_now=source == "live",
            )
        )
    if not chunks:
        raise ValueError("Schedule response must include at least one day")

    pages = _pack_chunks(chunks, body_limit)
    return tuple(
        _render_page(header_markup, header_plain, page, footer, footer_gap)
        for page in pages
    )


def _day_chunks(
    day: ScheduleDay,
    role: ViewerRole,
    zone: ZoneInfo,
    current_date: date,
    body_limit: int,
    *,
    suppress_empty_today_now: bool,
    show_now: bool,
) -> list[tuple[_TextLine, ...]]:
    heading = format_compact_schedule_date(day.date, reference_date=current_date)
    heading_line = _TextLine(f"<b>{heading}</b>", heading)
    is_current_day = day.date == current_date

    if not day.items:
        content = [_TextLine("No events", "No events")]
        if is_current_day and show_now and not suppress_empty_today_now:
            content.append(_now_divider())
    elif is_current_day:
        content = [
            _render_item(item, role, zone, current_date)
            for item in day.items
            if not show_now or item.status == "past"
        ]
        if show_now:
            content.append(_now_divider())
            content.extend(
                _render_item(item, role, zone, current_date)
                for item in day.items
                if item.status != "past"
            )
    else:
        content = [_render_item(item, role, zone, current_date) for item in day.items]

    chunks: list[tuple[_TextLine, ...]] = []
    current = [heading_line]
    for line in content:
        candidate = (*current, line)
        if _lines_utf16_length(candidate) <= body_limit:
            current.append(line)
            continue
        if len(current) == 1:
            raise ValueError("A schedule row exceeds the Telegram message budget")
        chunks.append(tuple(current))
        current = [heading_line, line]
        if _lines_utf16_length(current) > body_limit:
            raise ValueError("A schedule row exceeds the Telegram message budget")
    if len(current) > 1 or not chunks:
        chunks.append(tuple(current))
    return chunks


def _render_item(
    item: ScheduleItem,
    role: ViewerRole,
    zone: ZoneInfo,
    reference_date: date,
) -> _TextLine:
    start = item.start.astimezone(zone)
    end = item.end.astimezone(zone)
    start_clock = format_human_time(start)
    end_clock = format_human_time(end)
    if start.date() == end.date():
        interval = f"{start_clock}–{end_clock}"
    else:
        start_date = format_compact_schedule_date(
            start.date(), reference_date=reference_date
        )
        end_date = format_compact_schedule_date(
            end.date(), reference_date=reference_date
        )
        interval = f"{start_date} {start_clock}–{end_date} {end_clock}"

    event_label = (
        _AUTOMATIC_BREAK_LABELS[role]
        if item.source_kind == "automatic_break"
        else _EVENT_LABELS[item.event_type]
    )
    subject = ""
    markup_subject = ""
    if role == "TEACHER" and item.event_type == "LESSON":
        if item.subject_label is None:
            raise ValueError("Teacher lesson response is missing its subject label")
        safe_label = _safe_label(item.subject_label)
        subject = f" — {safe_label}"
        markup_subject = f" — {escape(safe_label, quote=False)}"

    status = _STATUS_MARKERS[item.status]
    plain = f"{interval}  {event_label}{subject}  {status}"
    markup = f"{escape(interval, quote=False)}  {event_label}{markup_subject}  {status}"
    return _TextLine(markup, plain)


def _is_stale(generated_at: datetime, now: datetime | None) -> bool:
    return (now or datetime.now(UTC)) - generated_at > STALE_SNAPSHOT_AFTER


def _now_divider() -> _TextLine:
    return _TextLine("── <b>NOW</b> ──", "── NOW ──")


def _safe_label(value: str) -> str:
    single_line = "".join(
        " " if category(character) in {"Cc", "Cs"} else character for character in value
    )
    normalized = " ".join(single_line.split())
    if len(normalized) <= MAX_LABEL_CODEPOINTS:
        return normalized
    return f"{normalized[: MAX_LABEL_CODEPOINTS - 1]}…"


def _pack_chunks(
    chunks: list[tuple[_TextLine, ...]], body_limit: int
) -> list[tuple[tuple[_TextLine, ...], ...]]:
    pages: list[tuple[tuple[_TextLine, ...], ...]] = []
    current: list[tuple[_TextLine, ...]] = []
    for chunk in chunks:
        candidate = (*current, chunk)
        if _chunks_utf16_length(candidate) <= body_limit:
            current.append(chunk)
            continue
        if not current:
            raise ValueError("A schedule date exceeds the Telegram message budget")
        pages.append(tuple(current))
        current = [chunk]
    if current:
        pages.append(tuple(current))
    return pages


def _render_page(
    header_markup: str,
    header_plain: str,
    chunks: tuple[tuple[_TextLine, ...], ...],
    footer: tuple[_TextLine, ...],
    footer_gap: str,
) -> str:
    body_markup = "\n\n".join(_lines_markup(chunk) for chunk in chunks)
    body_plain = "\n\n".join(_lines_plain(chunk) for chunk in chunks)
    text = f"{header_markup}\n{body_markup}" if header_markup else body_markup
    visible_text = f"{header_plain}\n{body_plain}" if header_plain else body_plain
    if footer:
        text += f"{footer_gap}{_lines_markup(footer)}"
        visible_text += f"{footer_gap}{_lines_plain(footer)}"
    if _utf16_length(visible_text) > MAX_MESSAGE_UTF16_UNITS:
        raise ValueError("Formatted schedule exceeds the Telegram message budget")
    return text


def _chunks_utf16_length(chunks: tuple[tuple[_TextLine, ...], ...]) -> int:
    return _utf16_length("\n\n".join(_lines_plain(chunk) for chunk in chunks))


def _lines_utf16_length(lines: Sequence[_TextLine]) -> int:
    return _utf16_length(_lines_plain(lines))


def _lines_markup(lines: Sequence[_TextLine]) -> str:
    return "\n".join(line.markup for line in lines)


def _lines_plain(lines: Sequence[_TextLine]) -> str:
    return "\n".join(line.plain for line in lines)


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
