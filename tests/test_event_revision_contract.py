from datetime import datetime
from uuid import UUID

from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.revisions import build_event_revision

_TEACHER_ID = UUID("00000000-0000-0000-0000-000000000001")
_STUDENT_ID = UUID("00000000-0000-0000-0000-000000000002")
_UPDATED_AT = datetime(2026, 1, 2, 3, 4, 5)


def test_one_off_event_revision_is_stable() -> None:
    event = Event(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        teacher_id=_TEACHER_ID,
        user_id=_STUDENT_ID,
        event_type="LESSON",
        start=datetime(2026, 3, 1, 9),
        end=datetime(2026, 3, 1, 10),
        post_break_minutes=5,
        updated_at=_UPDATED_AT,
    )

    assert (
        build_event_revision(event, [])
        == "8f2e1bfdcca4f3f62a80de98a4c106d37c97a347c4cc8af880d03f29b08375bf"
    )


def test_finite_recurring_event_revision_is_stable() -> None:
    event = RecurrentEvent(
        id=UUID("00000000-0000-0000-0000-000000000020"),
        teacher_id=_TEACHER_ID,
        user_id=_STUDENT_ID,
        event_type="LESSON",
        start=datetime(2026, 3, 1, 9),
        end=datetime(2026, 3, 1, 10),
        interval=7,
        interval_end=datetime(2026, 12, 1, 9),
        recurrence_mode="UTC_STEP",
        recurrence_timezone=None,
        post_break_minutes=5,
        updated_at=_UPDATED_AT,
    )

    assert (
        build_event_revision(event, [])
        == "d5b3200e6ee813170c608f764871d632d2aa58bcc4eb4658e7d476fef7ead3e1"
    )


def test_open_ended_event_revision_with_cancellations_is_stable() -> None:
    event = RecurrentEvent(
        id=UUID("00000000-0000-0000-0000-000000000030"),
        teacher_id=_TEACHER_ID,
        user_id=_STUDENT_ID,
        event_type="LESSON",
        start=datetime(2026, 3, 1, 9),
        end=datetime(2026, 3, 1, 10),
        interval=7,
        interval_end=None,
        recurrence_mode="TEACHER_LOCAL",
        recurrence_timezone="America/New_York",
        post_break_minutes=5,
        updated_at=_UPDATED_AT,
    )
    cancellations = [
        RecurrentCancels(
            id=UUID("00000000-0000-0000-0000-000000000031"),
            recurrent_event_id=event.id,
            start=datetime(2026, 3, 8, 9),
            end=datetime(2026, 3, 8, 10),
            updated_at=datetime(2026, 2, 1, 8, 30),
        ),
        RecurrentCancels(
            id=UUID("00000000-0000-0000-0000-000000000032"),
            recurrent_event_id=event.id,
            start=datetime(2026, 3, 15, 9),
            end=datetime(2026, 3, 15, 10),
            updated_at=datetime(2026, 2, 2, 8, 30),
        ),
    ]

    assert (
        build_event_revision(event, cancellations)
        == "787ca2ba4c67cb307db026eeea6bf89eff8b3e7478433ae79eac919e7cd05bdc"
    )
