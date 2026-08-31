from backend.exceptions import ValidationError
from backend.lessons.models import Event
from backend.validators import (
    NoConflictValidation,
    TimeValidation,
    UserExistsValidation,
)


class LessonValidator:
    def __init__(self):
        self.validators = [
            TimeValidation(),
            UserExistsValidation(),
            NoConflictValidation(),
        ]

    async def validate_all(self, session, data, user_id=None):
        for validator in self.validators:
            await validator.validate(session, data, user_id)


class LessonExistsValidator:
    async def validate(self, session, lesson_id):
        lesson = await session.get(Event, lesson_id)
        if not lesson:
            raise ValidationError("Lesson not found")
        return lesson


default_lesson_validator = LessonValidator()
exists_lesson_validator = LessonExistsValidator()
