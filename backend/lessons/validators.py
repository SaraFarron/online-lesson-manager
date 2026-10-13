from backend.events.validators import (
    EventExistsValidator,
    EventValidator,
)

default_lesson_validator = EventValidator(event_type="Lesson")
exists_lesson_validator = EventExistsValidator(event_type="Lesson")
