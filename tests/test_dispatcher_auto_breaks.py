from datetime import date, datetime
from random import randint

import pytest
from freezegun import freeze_time
from sqlalchemy.orm import Session

from src.db.models import Event, Executor, RecurrentEvent, User
from src.service.dispatcher.check_work_breaks import schedule_work_break


@pytest.fixture
def executor_with_schedule(db: Session) -> Executor:
    """Create an executor with work hours set via recurrent events."""
    exec_id = randint(10000, 99999)
    executor = Executor(
        code=str(exec_id),
        telegram_id=exec_id,
    )
    db.add(executor)
    db.commit()
    
    # Create a teacher user for this executor
    teacher = User(
        telegram_id=executor.telegram_id,
        username=f"teacher_{exec_id}",
        full_name=f"Teacher {exec_id}",
        role=User.Roles.TEACHER,
        executor_id=executor.id,
    )
    db.add(teacher)
    db.commit()
    
    # Create WORK_START recurrent event (09:00)
    work_start = RecurrentEvent(
        user_id=teacher.id,
        executor_id=executor.id,
        event_type=RecurrentEvent.EventTypes.WORK_START,
        start=datetime(2025, 9, 15, 9, 0),  # Monday
        end=datetime(2025, 9, 15, 9, 0),
        interval=7,  # Weekly
    )
    db.add(work_start)
    
    # Create WORK_END recurrent event (18:00)
    work_end = RecurrentEvent(
        user_id=teacher.id,
        executor_id=executor.id,
        event_type=RecurrentEvent.EventTypes.WORK_END,
        start=datetime(2025, 9, 15, 18, 0),  # Monday
        end=datetime(2025, 9, 15, 18, 0),
        interval=7,  # Weekly
    )
    db.add(work_end)
    db.commit()
    
    return executor


@pytest.fixture
def teacher_with_executor(db: Session, executor_with_schedule: Executor) -> User:
    """Get the teacher linked to an executor."""
    return db.query(User).filter(
        User.executor_id == executor_with_schedule.id,
        User.role == User.Roles.TEACHER,
    ).first()


@pytest.fixture
def student_with_teacher(db: Session, teacher_with_executor: User) -> User:
    """Create a student for the teacher."""
    student = User(
        telegram_id=randint(10000, 99999),
        username="student_1",
        full_name="Student One",
        role=User.Roles.STUDENT,
        executor_id=teacher_with_executor.executor_id,
    )
    db.add(student)
    db.commit()
    return student


@freeze_time("2025-09-14 09:00:00")
class TestScheduleWorkBreak:
    """Test schedule_work_break functionality."""

    def test_no_work_break_when_less_than_max_consecutive_lessons(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that no work break is created when there's only 1 lesson."""
        # Create 1 lesson (less than MAX_CONSECUTIVE_LESSONS)
        lesson = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
        )
        db.add(lesson)
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        assert result == []

    def test_no_work_break_when_lessons_not_consecutive(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that no work break is created when lessons have gaps between them."""
        # Create 2 lessons with a gap
        lesson1 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
        )
        lesson2 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 12, 0),  # 1 hour gap
            end=datetime(2025, 9, 15, 13, 0),
        )
        db.add_all([lesson1, lesson2])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        assert result == []

    def test_create_work_break_after_two_consecutive_simple_lessons(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that work break is created after 2 consecutive simple lessons."""
        # Create 2 consecutive lessons
        lesson1 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
        )
        lesson2 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 11, 0),  # Consecutive
            end=datetime(2025, 9, 15, 12, 0),
        )
        db.add_all([lesson1, lesson2])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        assert result
        assert len(result) == 1
        
        # Check work break is a simple Event (not recurrent)
        work_break = result[0]
        assert work_break.event_type == RecurrentEvent.EventTypes.WORK_BREAK
        assert work_break.start == datetime(2025, 9, 15, 12, 0)  # Right after last lesson
        assert work_break.end == datetime(2025, 9, 15, 12, 15)  # 15 minutes duration

    def test_create_recurrent_work_break_after_two_consecutive_recurrent_lessons(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that recurrent work break is created after 2 consecutive recurrent lessons."""
        # Create 2 consecutive recurrent lessons
        lesson1 = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 8, 10, 0),
            end=datetime(2025, 9, 8, 11, 0),
            interval=7,  # Weekly
        )
        lesson2 = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 11, 0),  # Consecutive
            end=datetime(2025, 9, 15, 12, 0),
            interval=7,  # Weekly
        )
        db.add_all([lesson1, lesson2])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        assert result is not None
        assert len(result) == 1
        
        # Check work break is a RecurrentEvent
        work_break = result[0]
        assert work_break.event_type == RecurrentEvent.EventTypes.WORK_BREAK
        assert work_break.start == datetime(2025, 9, 15, 12, 0)  # Right after last lesson
        assert work_break.end == datetime(2025, 9, 15, 12, 15)  # 15 minutes duration
        assert work_break.interval == 7  # Weekly

    def test_create_simple_work_break_when_mixed_lesson_types(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that simple work break is created when at least one lesson is not recurrent."""
        # Create 1 recurrent and 1 simple lesson consecutively
        lesson1 = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
            interval=7,
        )
        lesson2 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 11, 0),  # Consecutive
            end=datetime(2025, 9, 15, 12, 0),
        )
        db.add_all([lesson1, lesson2])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        assert result
        assert len(result) == 1
        
        # Check work break is a simple Event (not recurrent)
        work_break = result[0]
        assert work_break.event_type == RecurrentEvent.EventTypes.WORK_BREAK

    def test_create_work_break_after_more_than_two_consecutive_lessons(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that work break is created after 3+ consecutive lessons."""
        # Create 3 consecutive lessons
        lesson1 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
        )
        lesson2 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 11, 0),
            end=datetime(2025, 9, 15, 12, 0),
        )
        lesson3 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 12, 0),
            end=datetime(2025, 9, 15, 13, 0),
        )
        db.add_all([lesson1, lesson2, lesson3])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        assert result
        assert len(result) == 1
        
        work_break = result[0]
        assert work_break.start == datetime(2025, 9, 15, 13, 0)  # After last lesson
        assert work_break.end == datetime(2025, 9, 15, 13, 15)

    def test_no_work_break_when_work_end_gap_too_small(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
        teacher_with_executor: User,
    ):
        """Test that no work break is created when gap between last lesson and WORK_END is < LESSON_SIZE."""
        # Create WORK_END at 12:30 (only 30 minutes after last lesson)
        work_end = RecurrentEvent(
            user_id=teacher_with_executor.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.WORK_END,
            start=datetime(2025, 9, 15, 12, 30),
            end=datetime(2025, 9, 15, 12, 30),
            interval=7,
        )
        db.add(work_end)
        
        # Create 2 consecutive lessons ending at 12:00
        lesson1 = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
            interval=7,
        )
        lesson2 = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 11, 0),
            end=datetime(2025, 9, 15, 12, 0),
            interval=7,
        )
        db.add_all([lesson1, lesson2])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        # Gap is 30 minutes, which is less than LESSON_SIZE (1 hour)
        assert result == []

    def test_create_work_break_when_work_end_gap_sufficient(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
        teacher_with_executor: User,
    ):
        """Test that work break is created when gap between last lesson and WORK_END is >= LESSON_SIZE."""
        # Create WORK_END at 13:00 (1 hour after last lesson)
        work_end = RecurrentEvent(
            user_id=teacher_with_executor.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.WORK_END,
            start=datetime(2025, 9, 15, 13, 0),
            end=datetime(2025, 9, 15, 13, 0),
            interval=7,
        )
        db.add(work_end)
        
        # Create 2 consecutive lessons ending at 12:00
        lesson1 = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
            interval=7,
        )
        lesson2 = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 11, 0),
            end=datetime(2025, 9, 15, 12, 0),
            interval=7,
        )
        db.add_all([lesson1, lesson2])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        # Gap is exactly 1 hour (LESSON_SIZE), should create work break
        assert result is not None
        assert len(result) == 1
        
        work_break = result[0]
        assert work_break.start == datetime(2025, 9, 15, 12, 0)
        assert work_break.end == datetime(2025, 9, 15, 12, 15)

    def test_handle_multiple_groups_of_consecutive_lessons(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that multiple work breaks are created for multiple groups of consecutive lessons."""
        # First group: 2 consecutive lessons (10:00-12:00)
        lesson1 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
        )
        lesson2 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 11, 0),
            end=datetime(2025, 9, 15, 12, 0),
        )
        
        # Second group: 2 consecutive lessons (14:00-16:00) after a gap
        lesson3 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 14, 0),
            end=datetime(2025, 9, 15, 15, 0),
        )
        lesson4 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 15, 0),
            end=datetime(2025, 9, 15, 16, 0),
        )
        db.add_all([lesson1, lesson2, lesson3, lesson4])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        # Should create 2 work breaks
        assert result
        assert len(result) == 2
        
        # First work break after first group
        assert result[0].start == datetime(2025, 9, 15, 12, 0)
        assert result[0].end == datetime(2025, 9, 15, 12, 15)
        
        # Second work break after second group
        assert result[1].start == datetime(2025, 9, 15, 16, 0)
        assert result[1].end == datetime(2025, 9, 15, 16, 15)

    def test_work_break_includes_moved_lesson_type(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that MOVED_LESSON events are also counted as lessons for work breaks."""
        # Create 1 regular lesson and 1 moved lesson consecutively
        lesson1 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
        )
        lesson2 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.MOVED_LESSON,
            start=datetime(2025, 9, 15, 11, 0),
            end=datetime(2025, 9, 15, 12, 0),
        )
        db.add_all([lesson1, lesson2])
        db.commit()
        
        result = schedule_work_break(db, executor_with_schedule.id, date(2025, 9, 15))
        
        assert result
        assert len(result) == 1
        assert result[0].start == datetime(2025, 9, 15, 12, 0)
