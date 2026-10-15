from backend.constants import EventType
from backend.events.validators import (
    EventExistsValidator,
    EventValidator,
)

default_lesson_validator = EventValidator(
    event_type="Lesson", expected_event_type=EventType.LESSON
)
exists_lesson_validator = EventExistsValidator(
    event_type="Lesson", expected_event_type=EventType.LESSON
)
