from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from backend.breaks.schemas import (
    RecurrentBreakCreate,
    RecurrentBreakPublic,
    RecurrentBreakUpdate,
)
from backend.constants import EventType
from backend.events.constants import RecurrenceMode
from backend.events.management_schemas import EventManagementDetail
from backend.events.schemas import RecurrentEventCreate, RecurrentEventPublic
from backend.lessons.schemas import (
    RecurrentLessonCreate,
    RecurrentLessonPublic,
    RecurrentLessonUpdate,
)


def _create_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "start": datetime(2026, 10, 27, 9, tzinfo=UTC),
        "end": datetime(2026, 10, 27, 10, tzinfo=UTC),
        "user_id": uuid4(),
        "interval": 7,
    }
    fields.update(overrides)
    return fields


def test_weekly_lesson_create_defaults_to_an_open_ended_series() -> None:
    lesson = RecurrentLessonCreate.model_validate(_create_fields())

    assert lesson.interval_end is None
    assert "interval_end" not in lesson.model_fields_set
    assert lesson.model_dump(mode="json")["interval_end"] is None


def test_finite_weekly_lesson_cutoff_remains_offset_normalized() -> None:
    lesson = RecurrentLessonCreate.model_validate(
        _create_fields(
            interval_end=datetime(2026, 12, 1, 16, tzinfo=timezone(timedelta(hours=7)))
        )
    )

    assert lesson.interval_end == datetime(2026, 12, 1, 9, tzinfo=UTC)
    assert lesson.interval_end.utcoffset() == UTC.utcoffset(None)


@pytest.mark.parametrize(
    "fields",
    [
        {"interval": 1, "interval_end": None},
        {"event_type": EventType.BREAK, "interval_end": None},
        {"interval_end": datetime(2026, 10, 27, 9, tzinfo=UTC)},
    ],
)
def test_create_rejects_invalid_open_or_finite_cutoffs(
    fields: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        RecurrentLessonCreate.model_validate(_create_fields(**fields))


def test_weekly_recurrent_break_schemas_allow_a_null_cutoff() -> None:
    shared = RecurrentEventCreate.model_validate(
        _create_fields(event_type=EventType.BREAK)
    )
    assert shared.interval_end is None

    created = RecurrentBreakCreate.model_validate(_create_fields())
    assert created.interval_end is None

    updated = RecurrentBreakUpdate.model_validate(
        {
            **_create_fields(),
            "event_type": EventType.BREAK,
            "interval_end": None,
        }
    )
    assert updated.interval_end is None


@pytest.mark.parametrize(
    "fields",
    [
        {"event_type": EventType.BREAK, "interval": 1, "interval_end": None},
        {"event_type": EventType.VACATION, "interval": 7, "interval_end": None},
    ],
)
def test_nonweekly_break_and_vacation_null_cutoffs_are_rejected(
    fields: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        RecurrentEventCreate.model_validate(_create_fields(**fields))

    if fields["event_type"] == EventType.BREAK:
        with pytest.raises(PydanticValidationError):
            RecurrentBreakUpdate.model_validate(
                {
                    **_create_fields(interval=1),
                    "event_type": EventType.BREAK,
                    "interval_end": None,
                }
            )


def test_update_requires_explicit_cutoff_and_allows_clearing_weekly_lesson() -> None:
    fields = {
        "user_id": uuid4(),
        "start": datetime(2026, 10, 27, 9, tzinfo=UTC),
        "end": datetime(2026, 10, 27, 10, tzinfo=UTC),
        "interval": 7,
    }
    with pytest.raises(PydanticValidationError):
        RecurrentLessonUpdate.model_validate(fields)

    updated = RecurrentLessonUpdate.model_validate({**fields, "interval_end": None})
    assert updated.interval_end is None


def test_public_models_serialize_null_cutoff_as_json_null() -> None:
    fields = {
        "id": uuid4(),
        **_create_fields(),
        "event_type": EventType.LESSON,
        "interval_end": None,
    }

    assert (
        RecurrentEventPublic.model_validate(fields).model_dump(mode="json")[
            "interval_end"
        ]
        is None
    )
    assert (
        RecurrentLessonPublic.model_validate(fields).model_dump(mode="json")[
            "interval_end"
        ]
        is None
    )

    break_fields = {**fields, "event_type": EventType.BREAK}
    assert (
        RecurrentBreakCreate.model_validate(
            {
                **_create_fields(),
                "event_type": EventType.BREAK,
                "interval_end": None,
            }
        ).model_dump(mode="json")["interval_end"]
        is None
    )
    assert (
        RecurrentBreakPublic.model_validate(break_fields).model_dump(mode="json")[
            "interval_end"
        ]
        is None
    )


def test_management_detail_uses_recurrence_metadata_not_cutoff_to_discriminate() -> (
    None
):
    detail = EventManagementDetail(
        id=uuid4(),
        event_type=EventType.LESSON,
        start=datetime(2026, 10, 27, 9, tzinfo=UTC),
        end=datetime(2026, 10, 27, 10, tzinfo=UTC),
        user_id=uuid4(),
        subject_label="Lesson",
        is_recurrent=True,
        cancelled_occurrences=0,
        post_break_minutes=0,
        interval=7,
        interval_end=None,
        recurrence_mode=RecurrenceMode.TEACHER_LOCAL,
        recurrence_timezone="UTC",
        revision="a" * 64,
    )

    assert detail.interval_end is None
    assert detail.model_dump(mode="json")["interval_end"] is None

    with pytest.raises(PydanticValidationError):
        EventManagementDetail(
            **{
                **detail.model_dump(),
                "interval": 1,
            }
        )
