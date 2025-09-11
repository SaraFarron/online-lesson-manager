from datetime import datetime, time

from src.db.models import Event, RecurrentEvent
from src.service.lessons import LessonsService


def test_create_lesson(db_session, test_user):
    service = LessonsService(db_session)
    executor_id = test_user.executor_id
    date = datetime.now().date()
    time = "10:00"

    lesson = service.create_lesson(test_user.id, executor_id, date, time)

    assert lesson.user_id == test_user.id
    assert lesson.executor_id == executor_id
    assert lesson.event_type == Event.EventTypes.LESSON
    assert lesson.start.strftime("%H:%M") == "10:00"
    assert lesson.end.strftime("%H:%M") == "11:00"


def test_create_recurrent_lesson(db_session, test_user):
    service = LessonsService(db_session)
    executor_id = test_user.executor_id
    weekday = 2  # Wednesday
    lesson_time = time(14, 0)  # 2:00 PM

    recurrent_lesson = service.create_recurrent_lesson(
        user_id=test_user.id,
        executor_id=executor_id,
        weekday=weekday,
        time=lesson_time,
    )

    assert recurrent_lesson.user_id == test_user.id
    assert recurrent_lesson.executor_id == executor_id
    assert recurrent_lesson.event_type == RecurrentEvent.EventTypes.LESSON
    assert recurrent_lesson.start.weekday() == weekday
    assert recurrent_lesson.start.strftime("%H:%M") == "14:00"
    assert recurrent_lesson.end.strftime("%H:%M") == "15:00"
    assert recurrent_lesson.interval == 7
