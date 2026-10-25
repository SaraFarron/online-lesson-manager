from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.constants import EventType
from backend.events.occurrences import (
    Occurrence,
    OccurrenceSource,
    OccurrenceSourceKind,
)
from backend.schedule.presentation import shape_schedule
from backend.schedule.schemas import (
    ScheduleEventStatus,
    ScheduleItem,
    SchedulePeriod,
    TeacherLessonScheduleItem,
)

TEACHER_ID = UUID("00000000-0000-0000-0000-000000000001")
STUDENT_ID = UUID("00000000-0000-0000-0000-000000000002")


def _occurrence(
    source_id: int,
    start: datetime,
    end: datetime,
    *,
    event_type: EventType = EventType.LESSON,
    user_id: UUID = STUDENT_ID,
    source_kind: OccurrenceSourceKind = OccurrenceSourceKind.EVENT,
) -> Occurrence:
    return Occurrence(
        source=OccurrenceSource(
            kind=source_kind,
            id=UUID(f"00000000-0000-0000-0000-{source_id:012d}"),
            teacher_id=TEACHER_ID,
            user_id=user_id,
            event_type=event_type,
        ),
        start=start,
        end=end,
    )


def test_day_shapes_empty_and_ordered_items_with_one_clock_snapshot() -> None:
    now = datetime(2026, 10, 19, 2, tzinfo=UTC)
    response = shape_schedule(
        SchedulePeriod.DAY,
        date(2026, 10, 19),
        "Asia/Bangkok",
        now,
        [
            _occurrence(
                3,
                datetime(2026, 10, 19, 3, tzinfo=UTC),
                datetime(2026, 10, 19, 4, tzinfo=UTC),
            ),
            _occurrence(
                1,
                datetime(2026, 10, 19, 1, tzinfo=UTC),
                datetime(2026, 10, 19, 2, tzinfo=UTC),
            ),
            _occurrence(
                2,
                datetime(2026, 10, 19, 2, tzinfo=UTC),
                datetime(2026, 10, 19, 3, tzinfo=UTC),
            ),
        ],
    )

    assert [day.date for day in response.days] == [date(2026, 10, 19)]
    assert [item.source_id.int for item in response.days[0].items] == [1, 2, 3]
    assert [item.status for item in response.days[0].items] == [
        ScheduleEventStatus.PAST,
        ScheduleEventStatus.ONGOING,
        ScheduleEventStatus.UPCOMING,
    ]
    assert response.days[0].items[1].start == now


def test_empty_day_retains_its_date_with_no_items() -> None:
    response = shape_schedule(
        SchedulePeriod.DAY,
        date(2026, 10, 19),
        "UTC",
        datetime(2026, 10, 19, tzinfo=UTC),
        [],
    )

    assert len(response.days) == 1
    assert response.days[0].date == date(2026, 10, 19)
    assert response.days[0].items == []


def test_week_from_sunday_has_seven_days_and_groups_by_start_date_only() -> None:
    crossing_into_monday = _occurrence(
        1,
        datetime(2026, 10, 18, 23, 30, tzinfo=UTC),
        datetime(2026, 10, 19, 0, 30, tzinfo=UTC),
    )
    ending_at_midnight = _occurrence(
        2,
        datetime(2026, 10, 20, 23, tzinfo=UTC),
        datetime(2026, 10, 21, 0, tzinfo=UTC),
    )
    crossing_out_of_sunday = _occurrence(
        3,
        datetime(2026, 10, 25, 23, 30, tzinfo=UTC),
        datetime(2026, 10, 26, 0, 30, tzinfo=UTC),
    )
    starting_at_monday_midnight = _occurrence(
        4,
        datetime(2026, 10, 19, tzinfo=UTC),
        datetime(2026, 10, 19, 1, tzinfo=UTC),
    )
    response = shape_schedule(
        SchedulePeriod.WEEK,
        date(2026, 10, 25),
        "UTC",
        datetime(2026, 10, 21, tzinfo=UTC),
        [
            crossing_out_of_sunday,
            ending_at_midnight,
            crossing_into_monday,
            starting_at_monday_midnight,
        ],
    )

    assert [day.date for day in response.days] == [
        date(2026, 10, 19),
        date(2026, 10, 20),
        date(2026, 10, 21),
        date(2026, 10, 22),
        date(2026, 10, 23),
        date(2026, 10, 24),
        date(2026, 10, 25),
    ]
    assert [item.source_id.int for item in response.days[0].items] == [4]
    assert [item.source_id.int for item in response.days[1].items] == [2]
    assert response.days[2].items == []
    assert [item.source_id.int for item in response.days[-1].items] == [3]
    assert response.days[-1].items[0].end == datetime(2026, 10, 26, 0, 30, tzinfo=UTC)


def test_day_bounds_follow_viewer_dst_transition() -> None:
    response = shape_schedule(
        SchedulePeriod.DAY,
        date(2026, 3, 8),
        "America/New_York",
        datetime(2026, 3, 8, 7, tzinfo=UTC),
        [
            _occurrence(
                1,
                datetime(2026, 3, 8, 6, 30, tzinfo=UTC),
                datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
            )
        ],
    )

    assert response.range_start == datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert response.range_end == datetime(2026, 3, 9, 4, tzinfo=UTC)
    item = response.days[0].items[0]
    assert item.status == ScheduleEventStatus.ONGOING
    assert item.model_dump(mode="json")["start"] == "2026-03-08T06:30:00+00:00"


def test_teacher_lesson_metadata_is_explicit_and_forbidden_on_student_item() -> None:
    response = shape_schedule(
        SchedulePeriod.DAY,
        date(2026, 10, 19),
        "UTC",
        datetime(2026, 10, 19, tzinfo=UTC),
        [
            _occurrence(
                1,
                datetime(2026, 10, 19, 9, tzinfo=UTC),
                datetime(2026, 10, 19, 10, tzinfo=UTC),
            )
        ],
        subject_labels={STUDENT_ID: "Alice Example"},
    )
    teacher_item = response.days[0].items[0]

    assert isinstance(teacher_item, TeacherLessonScheduleItem)
    assert teacher_item.subject_label == "Alice Example"
    assert "subject_label" in teacher_item.model_dump(mode="json")

    student_response = shape_schedule(
        SchedulePeriod.DAY,
        date(2026, 10, 19),
        "UTC",
        datetime(2026, 10, 19, tzinfo=UTC),
        [
            _occurrence(
                2,
                datetime(2026, 10, 19, 9, tzinfo=UTC),
                datetime(2026, 10, 19, 10, tzinfo=UTC),
            )
        ],
    )
    student_item = student_response.days[0].items[0]
    assert isinstance(student_item, ScheduleItem)
    assert not isinstance(student_item, TeacherLessonScheduleItem)
    assert "subject_label" not in student_item.model_dump(mode="json")

    with pytest.raises(ValidationError):
        ScheduleItem.model_validate(
            {
                "source_kind": "event",
                "source_id": "00000000-0000-0000-0000-000000000001",
                "event_type": "LESSON",
                "start": "2026-10-19T09:00:00Z",
                "end": "2026-10-19T10:00:00Z",
                "status": "upcoming",
                "subject_label": "Alice Example",
            }
        )


def test_teacher_lesson_item_rejects_non_lesson_event_type() -> None:
    with pytest.raises(ValidationError):
        TeacherLessonScheduleItem(
            source_kind=OccurrenceSourceKind.EVENT,
            source_id=UUID("00000000-0000-0000-0000-000000000001"),
            event_type=EventType.BREAK,
            start=datetime(2026, 10, 19, 9, tzinfo=UTC),
            end=datetime(2026, 10, 19, 10, tzinfo=UTC),
            status=ScheduleEventStatus.UPCOMING,
            subject_label="Should not be serialized",
        )


def test_automatic_break_schedule_item_requires_parent_identity() -> None:
    automatic_break = ScheduleItem(
        source_kind=OccurrenceSourceKind.AUTOMATIC_BREAK,
        source_id=UUID("00000000-0000-0000-0000-000000000010"),
        event_type=EventType.BREAK,
        parent_kind=OccurrenceSourceKind.RECURRENT_EVENT,
        parent_start=datetime(2026, 10, 19, 9, tzinfo=UTC),
        start=datetime(2026, 10, 19, 10, tzinfo=UTC),
        end=datetime(2026, 10, 19, 10, 5, tzinfo=UTC),
        status=ScheduleEventStatus.UPCOMING,
    )

    assert automatic_break.model_dump(mode="json")["parent_start"] == (
        "2026-10-19T09:00:00+00:00"
    )
    with pytest.raises(ValidationError):
        ScheduleItem(
            source_kind=OccurrenceSourceKind.AUTOMATIC_BREAK,
            source_id=UUID("00000000-0000-0000-0000-000000000010"),
            event_type=EventType.BREAK,
            start=datetime(2026, 10, 19, 10, tzinfo=UTC),
            end=datetime(2026, 10, 19, 10, 5, tzinfo=UTC),
            status=ScheduleEventStatus.UPCOMING,
        )
