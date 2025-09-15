from datetime import datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from src.db.models import CancelledRecurrentEvent, Event, RecurrentEvent, User
from src.service.lessons import LessonsService


def test_create_lesson(db: Session, test_user: User):
    service = LessonsService(db)
    executor_id = test_user.executor_id
    date = datetime.now().date()
    time = "14:00"

    lesson = service.create_lesson(test_user.id, executor_id, date, time)

    assert lesson.user_id == test_user.id
    assert lesson.executor_id == executor_id
    assert lesson.event_type == Event.EventTypes.LESSON
    assert lesson.start.strftime("%H:%M") == time
    assert lesson.end.strftime("%H:%M") == "15:00"


def test_create_recurrent_lesson(db: Session, test_user: User):
    service = LessonsService(db)
    executor_id = test_user.executor_id
    weekday = 2  # Wednesday
    week = 7
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
    assert recurrent_lesson.interval == week


def test_student_cannot_create_overlapping_lesson(db: Session, test_student: User, test_teacher: User):
    service = LessonsService(db)
    executor_id = test_teacher.executor_id
    date = datetime.now().date()
    lesson_time = "14:00"

    # Create an existing lesson for the teacher
    service.create_lesson(test_teacher.id, executor_id, date, lesson_time)

    # Attempt to create an overlapping lesson as a student
    with pytest.raises(ValueError, match="Lesson overlaps with an existing event"):
        service.create_lesson(test_student.id, executor_id, date, lesson_time)


def test_student_cannot_create_lesson_within_3_hours(db: Session, test_student: User, test_teacher: User):
    service = LessonsService(db)
    executor_id = test_teacher.executor_id
    now = datetime.now()
    date = now.date()
    lesson_time = (now + timedelta(hours=2)).strftime("%H:%M")  # Within 3 hours

    # Attempt to create a lesson within 3 hours
    with pytest.raises(ValueError, match="Lesson overlaps with an existing event"):
        service.create_lesson(test_student.id, executor_id, date, lesson_time)


def test_student_can_create_lesson_after_3_hours(db: Session, test_student: User, test_teacher: User):
    service = LessonsService(db)
    executor_id = test_teacher.executor_id
    now = datetime.now()
    date = now.date()
    lesson_time = (now + timedelta(hours=4)).strftime("%H:%M")  # After 3 hours

    # Create a lesson after 3 hours
    lesson = service.create_lesson(test_student.id, executor_id, date, lesson_time)

    assert lesson.user_id == test_student.id
    assert lesson.executor_id == executor_id
    assert lesson.event_type == Event.EventTypes.LESSON


def test_recurrent_lesson_cancellation_creates_cancelled_event(db: Session, test_student: User, test_teacher: User):
    service = LessonsService(db)
    executor_id = test_teacher.executor_id
    weekday = 2  # Wednesday
    lesson_time = time(14, 0)  # 2:00 PM

    # Create a recurrent lesson
    recurrent_lesson = service.create_recurrent_lesson(
        user_id=test_student.id,
        executor_id=executor_id,
        weekday=weekday,
        time=lesson_time,
    )

    # Cancel the recurrent lesson for a specific period
    cancel_dt = datetime.combine(datetime.now().date(), lesson_time)
    new_dt = cancel_dt + timedelta(days=1)
    service.move_recurrent_lesson_once(recurrent_lesson.id, cancel_dt.date(), new_dt.date(), new_dt)

    # Check that a CancelledRecurrentEvent was created
    cancelled_event = db.query(CancelledRecurrentEvent).filter_by(event_id=recurrent_lesson.id).first()
    assert cancelled_event is not None
    assert cancelled_event.start == cancel_dt
    assert cancelled_event.end == cancel_dt + timedelta(hours=1)


def test_no_more_than_two_lessons_in_a_row(db: Session, test_teacher: User):
    service = LessonsService(db)
    executor_id = test_teacher.executor_id
    date = datetime.now().date() + timedelta(days=3)

    # Create two consecutive lessons
    service.create_lesson(test_teacher.id, executor_id, date, "10:00")
    service.create_lesson(test_teacher.id, executor_id, date, "11:00")

    # Attempt to create a third consecutive lesson
    with pytest.raises(ValueError, match="Lesson overlaps with an existing event"):
        service.create_lesson(test_teacher.id, executor_id, date, "12:00")


def test_break_event_created_after_two_lessons_in_a_row(db: Session, test_teacher: User):
    service = LessonsService(db)
    executor_id = test_teacher.executor_id
    date = datetime.now().date()

    # Create two consecutive lessons
    service.create_lesson(test_teacher.id, executor_id, date, "10:00")
    service.create_lesson(test_teacher.id, executor_id, date, "11:00")

    # Check that a break event is created after the second lesson
    break_event = db.query(RecurrentEvent).filter_by(event_type=RecurrentEvent.EventTypes.WORK_BREAK).first()
    assert break_event is not None
    assert break_event.start.strftime("%H:%M") == "12:00"
    assert break_event.end.strftime("%H:%M") == "13:00"

