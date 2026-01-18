from datetime import datetime, time
from random import randint

import pytest
from freezegun import freeze_time
from sqlalchemy.orm import Session

from src.core.config import MAX_LESSONS_PER_DAY
from src.db.models import Event, Executor, RecurrentEvent, User
from src.service.pipelines.add_lesson import AddLessonPipeline


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
    
    # Create a teacher user for this executor (required for recurrent events)
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
    """Get the teacher linked to an executor (already created in executor_with_schedule)."""
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


@pytest.fixture
def second_executor_with_schedule(db: Session) -> Executor:
    """Create a second executor with work hours set via recurrent events."""
    exec_id = randint(10000, 99999)
    executor = Executor(
        code=str(exec_id),
        telegram_id=exec_id,
    )
    db.add(executor)
    db.commit()
    
    # Create a teacher user for this executor (required for recurrent events)
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
def second_teacher_with_executor(db: Session, second_executor_with_schedule: Executor) -> User:
    """Get the second teacher linked to a different executor."""
    return db.query(User).filter(
        User.executor_id == second_executor_with_schedule.id,
        User.role == User.Roles.TEACHER,
    ).first()


@pytest.fixture
def second_student_with_teacher(db: Session, second_teacher_with_executor: User) -> User:
    """Create a student for the second teacher."""
    student = User(
        telegram_id=randint(10000, 99999),
        username="student_2",
        full_name="Student Two",
        role=User.Roles.STUDENT,
        executor_id=second_teacher_with_executor.executor_id,
    )
    db.add(student)
    db.commit()
    return student


@freeze_time("2025-09-14 09:00:00")
class TestAddLessonPipeline:
    """Test AddLessonPipeline functionality."""

    def test_choose_lesson_date_returns_available_time(
        self,
        db: Session,
        student_with_teacher: User,
    ):
        """Test that choosing a lesson date returns available time slots."""
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        available_time = pipeline.choose_lesson_date("15.09")
        
        assert available_time is not None
        assert isinstance(available_time, list)
        assert len(available_time) > 0

    def test_cannot_create_lesson_in_past(
        self,
        db: Session,
        student_with_teacher: User,
    ):
        """Test that cannot create lesson in the past."""
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        with pytest.raises(AssertionError):
            pipeline.choose_lesson_date("13.09")

    def test_create_lesson_in_available_slot(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that lesson can be created in an available time slot."""
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        pipeline.choose_lesson_date("15.09")
        lesson_time = time(hour=10, minute=0)
        
        result = pipeline.choose_lesson_time(lesson_time)
        
        assert result is not None
        lessons = db.query(Event).filter(
            Event.user_id == student_with_teacher.id,
            Event.executor_id == executor_with_schedule.id,
        ).all()
        assert len(lessons) == 1
        assert lessons[0].start.time() == lesson_time

    def test_cannot_create_lesson_in_occupied_slot(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that cannot create lesson in already occupied time slot."""
        # Create existing lesson
        existing_lesson = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
        )
        db.add(existing_lesson)
        db.commit()
        
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        pipeline.choose_lesson_date("15.09")
        lesson_time = time(hour=10, minute=0)
        
        with pytest.raises(AssertionError):
            pipeline.choose_lesson_time(lesson_time)

    def test_time_slots_exclusive_per_executor(
        self,
        db: Session,
        student_with_teacher: User,
        second_student_with_teacher: User,
        executor_with_schedule: Executor,
        second_executor_with_schedule: Executor,
    ):
        """Test that time slots are exclusive per executor - one executor's occupied slot doesn't affect another."""
        # Create lesson for first executor at 10:00
        lesson_executor_1 = Event(
            user_id=student_with_teacher.id,
            executor_id=executor_with_schedule.id,
            event_type=Event.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
        )
        db.add(lesson_executor_1)
        db.commit()
        
        # Try to create lesson for second executor at the same time
        pipeline_2 = AddLessonPipeline(
            db=db,
            user_id=second_student_with_teacher.id,
            telegram_id=second_student_with_teacher.telegram_id,
        )
        
        pipeline_2.choose_lesson_date("15.09")
        lesson_time = time(hour=10, minute=0)
        
        # Should be able to create lesson at same time for different executor
        result = pipeline_2.choose_lesson_time(lesson_time)
        
        assert result is not None
        lessons_executor_2 = db.query(Event).filter(
            Event.executor_id == second_executor_with_schedule.id,
            Event.start == datetime(2025, 9, 15, 10, 0),
        ).all()
        assert len(lessons_executor_2) == 1

    def test_cannot_create_more_than_max_lessons_per_day(
        self,
        db: Session,
        student_with_teacher: User,
        executor_with_schedule: Executor,
    ):
        """Test that cannot create more than MAX_LESSONS_PER_DAY lessons on the same day."""
        # Create MAX_LESSONS_PER_DAY lessons
        for i in range(MAX_LESSONS_PER_DAY):
            lesson = Event(
                user_id=student_with_teacher.id,
                executor_id=executor_with_schedule.id,
                event_type=Event.EventTypes.LESSON,
                start=datetime(2025, 9, 15, 9 + i, 0),
                end=datetime(2025, 9, 15, 10 + i, 0),
            )
            db.add(lesson)
        db.commit()
        
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        lessons = pipeline.choose_lesson_date("15.09")
        lesson_time = time(hour=15, minute=0)
        
        assert not lessons  # Should be empty as no slots available
        with pytest.raises(AssertionError):
            pipeline.choose_lesson_time(lesson_time)

    def test_cannot_create_lesson_within_hrs_to_cancel(
        self,
        db: Session,
        student_with_teacher: User,
    ):
        """Test that cannot create lesson if start time is <= now + HRS_TO_CANCEL."""
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        # Current frozen time is 2025-09-14 09:00:00
        # Try to create lesson at 11:00 on same day (only 2 hours away, but HRS_TO_CANCEL is 3)
        pipeline.choose_lesson_date("14.09")
        lesson_time = time(hour=11, minute=0)
        
        with pytest.raises(AssertionError):
            pipeline.choose_lesson_time(lesson_time)

    def test_can_create_lesson_after_hrs_to_cancel(
        self,
        db: Session,
        student_with_teacher: User,
    ):
        """Test that can create lesson if start time is > now + HRS_TO_CANCEL."""
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        # Current frozen time is 2025-09-14 09:00:00
        # Create lesson at 13:00 (4 hours away, more than HRS_TO_CANCEL=3)
        pipeline.choose_lesson_date("14.09")
        lesson_time = time(hour=13, minute=0)
        
        result = pipeline.choose_lesson_time(lesson_time)
        
        assert result is not None
        lessons = db.query(Event).filter(
            Event.user_id == student_with_teacher.id,
            Event.start == datetime(2025, 9, 14, 13, 0),
        ).all()
        assert len(lessons) == 1

    def test_available_slots_respect_recurrent_lessons(
        self,
        db: Session,
        student_with_teacher: User,
    ):
        """Test that available time slots respect existing recurrent lessons."""
        # Create a recurrent lesson on Mondays at 10:00 (2025-09-15 is Monday)
        recurrent_lesson = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=student_with_teacher.executor_id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 10, 0),
            end=datetime(2025, 9, 15, 11, 0),
            interval=7,
        )
        db.add(recurrent_lesson)
        db.commit()
        
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        pipeline.choose_lesson_date("15.09")
        lesson_time = time(hour=10, minute=0)
        
        # Should not be able to create lesson at time occupied by recurrent lesson
        with pytest.raises(AssertionError):
            pipeline.choose_lesson_time(lesson_time)

    def test_max_lessons_includes_all_lesson_types(
        self,
        db: Session,
        student_with_teacher: User,
    ):
        """Test that MAX_LESSONS_PER_DAY limit includes both regular and recurrent lessons."""
        # Create MAX_LESSONS_PER_DAY - 1 regular lessons
        for i in range(MAX_LESSONS_PER_DAY - 1):
            lesson = Event(
                user_id=student_with_teacher.id,
                executor_id=student_with_teacher.executor_id,
                event_type=Event.EventTypes.LESSON,
                start=datetime(2025, 9, 15, 9 + i, 0),
                end=datetime(2025, 9, 15, 10 + i, 0),
            )
            db.add(lesson)
        
        # Create 1 recurrent lesson for the same day
        recurrent_lesson = RecurrentEvent(
            user_id=student_with_teacher.id,
            executor_id=student_with_teacher.executor_id,
            event_type=RecurrentEvent.EventTypes.LESSON,
            start=datetime(2025, 9, 15, 15, 0),
            end=datetime(2025, 9, 15, 16, 0),
            interval=7,
        )
        db.add(recurrent_lesson)
        db.commit()
        
        pipeline = AddLessonPipeline(
            db=db,
            user_id=student_with_teacher.id,
            telegram_id=student_with_teacher.telegram_id,
        )
        
        pipeline.choose_lesson_date("15.09")
        lesson_time = time(hour=16, minute=0)
        
        # Should raise error as we already have MAX_LESSONS_PER_DAY
        with pytest.raises(AssertionError):
            pipeline.choose_lesson_time(lesson_time)
