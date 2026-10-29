import hashlib
import json
from datetime import datetime

from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.time_utils import from_storage_utc, normalize_utc
from backend.exceptions import Conflict


def _assert_expected_revision(expected: str, current: str) -> None:
    if expected != current:
        raise Conflict(
            {
                "code": "stale_event_revision",
                "message": "This event changed; reload it before editing",
            }
        )


def build_event_revision(
    event: Event | RecurrentEvent,
    cancellations: tuple[RecurrentCancels, ...] | list[RecurrentCancels],
) -> str:
    payload: dict[str, object] = {
        "id": str(event.id),
        "teacher_id": str(event.teacher_id),
        "user_id": str(event.user_id),
        "event_type": event.event_type,
        "start": _timestamp(event.start),
        "end": _timestamp(event.end),
        "post_break_minutes": event.post_break_minutes,
        "updated_at": normalize_utc(event.updated_at).isoformat(),
        "cancellations": [
            {
                "id": str(cancel.id),
                "start": _timestamp(cancel.start),
                "end": _timestamp(cancel.end),
                "updated_at": normalize_utc(cancel.updated_at).isoformat(),
            }
            for cancel in cancellations
        ],
    }
    if isinstance(event, RecurrentEvent):
        payload.update(
            {
                "interval": event.interval,
                "interval_end": _timestamp(event.interval_end),
                "recurrence_mode": event.recurrence_mode,
                "recurrence_timezone": event.recurrence_timezone,
            }
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return from_storage_utc(value).isoformat()
