from backend.events.validators import (
    EventExistsValidator,
    EventValidator,
)

default_vacation_validator = EventValidator(event_type="Vacation")
exists_vacation_validator = EventExistsValidator(event_type="Vacation")
