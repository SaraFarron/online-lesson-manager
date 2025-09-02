from datetime import datetime

from src.db.models import Event
from src.service.lessons import LessonsService


def test_create_lesson(db_session, test_user):
    # Arrange
    service = LessonsService(db_session)
    executor_id = test_user.executor_id
    date = datetime.now().date()
    time = "10:00"

    # Act
    lesson = service.create_lesson(test_user.id, executor_id, date, time)

    # Assert
    assert lesson.user_id == test_user.id
    assert lesson.executor_id == executor_id
    assert lesson.event_type == Event.EventTypes.LESSON
    assert lesson.start.strftime("%H:%M") == "10:00"
    assert lesson.end.strftime("%H:%M") == "11:00"
