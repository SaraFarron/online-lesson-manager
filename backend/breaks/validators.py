from backend.constants import EventType
from backend.events.validators import (
    EventExistsValidator,
    EventValidator,
)

default_break_validator = EventValidator(event_type="Break")
exists_break_validator = EventExistsValidator(
    event_type="Break", expected_event_type=EventType.BREAK
)
