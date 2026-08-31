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


default_lesson_validator = LessonValidator()
