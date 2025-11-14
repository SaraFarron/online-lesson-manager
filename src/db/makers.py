from datetime import date, datetime
from datetime import time as _time

from sqlalchemy.orm import Session

from core import config
from db.models import Event


def create_lesson(db: Session, user_id: int, executor_id: int, date: date, time: _time) -> Event:
    start = datetime.combine(date, time)
    end = start + config.LESSON_SIZE
    new_lesson = Event(
        user_id=user_id,
        executor_id=executor_id,
        event_type=Event.EventTypes.LESSON,
        start=start,
        end=end,
    )
    db.add(new_lesson)
    db.commit()
    return new_lesson
