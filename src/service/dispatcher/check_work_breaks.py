from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from core.config import LESSON_SIZE, MAX_CONSECUTIVE_LESSONS
from db.models import Event, RecurrentEvent, User
from service.services import EventService
from src.db.schemas import EventSchema, RecurrentEventSchema


def form_consecutive_groups(i: int, lessons: list[EventSchema | RecurrentEventSchema]):
    consecutive_group = [lessons[i]]
    j = i + 1
    while j < len(lessons):
        end_mins = consecutive_group[-1].end.hour * 100 + consecutive_group[-1].end.minute
        start_mins = lessons[j].start.hour * 100 + lessons[j].start.minute
        if abs(end_mins - start_mins) < 1:
            consecutive_group.append(lessons[j])
            j += 1
        else:
            break
    return j, consecutive_group


def search_work_end_event(
    events: list[EventSchema | RecurrentEventSchema],
    last_lesson: EventSchema | RecurrentEventSchema,
):
    work_end_event = None
    for event in events:
        if (
            isinstance(event, RecurrentEventSchema) and
            event.event_type == RecurrentEvent.EventTypes.WORK_END and
            event.start > last_lesson.end
        ):
            work_end_event = event
            break
    return work_end_event


def create_work_break(user_id: int, executor_id: int, start: datetime, recurrent: bool):
    if recurrent:
        return RecurrentEvent(
            user_id=user_id,
            executor_id=executor_id,
            event_type=RecurrentEvent.EventTypes.WORK_BREAK,
            start=start,
            end=start + timedelta(minutes=15),
            interval=7,
    )
    return Event(
        user_id=user_id,
        executor_id=executor_id,
        event_type=RecurrentEvent.EventTypes.WORK_BREAK,
        start=start,
        end=start + timedelta(minutes=15),
    )


def create_work_breaks(
    db: Session,
    lessons: list[EventSchema | RecurrentEventSchema],
    events: list[EventSchema | RecurrentEventSchema],
    executor_id: int,
    user_id: int,
):
    work_breaks = []
    created_work_breaks = {e.start for e in events if e.event_type == RecurrentEvent.EventTypes.WORK_BREAK}
    i = 0
    
    while i < len(lessons):
        j, consecutive_group = form_consecutive_groups(i, lessons)
        
        # Check if we have enough consecutive lessons
        if len(consecutive_group) >= MAX_CONSECUTIVE_LESSONS:
            last_lesson = consecutive_group[-1]
            work_break_start = last_lesson.end
            # Check if all lessons are recurrent (RecurrentEventSchema)
            all_recurrent = all(isinstance(lesson, RecurrentEventSchema) for lesson in consecutive_group)
            should_create_break = True
            
            # Check if there's a WORK_END event after the last lesson
            work_end_event = search_work_end_event(events, last_lesson)
            
            # If there's a WORK_END event, check the gap
            if work_end_event:
                time_gap = work_end_event.start - last_lesson.end
                should_create_break = time_gap >= LESSON_SIZE
            
            # If work_break was already created
            if work_break_start in created_work_breaks:
                should_create_break = False
            
            if should_create_break:
                work_break = create_work_break(
                    user_id, executor_id, work_break_start, all_recurrent,
                )
                db.add(work_break)
                work_breaks.append(work_break)
        
        # Move to next potential group
        i = max(i + 1, j)

    db.commit()
    return work_breaks


def schedule_work_break(db: Session, executor_id: int, day: date) -> list[Event | RecurrentEvent]:
    lesson_types = (
        Event.EventTypes.LESSON,
        Event.EventTypes.MOVED_LESSON,
        RecurrentEvent.EventTypes.LESSON,
    )
    service = EventService(db)
    events = service.day_schedule(executor_id, day)
    lessons = [e for e in events if e.event_type in lesson_types]
    executor_user = db.query(User).filter(
        User.executor_id == executor_id, User.role == User.Roles.TEACHER,
    ).first()
    
    if len(lessons) < MAX_CONSECUTIVE_LESSONS:
        return []
    
    return create_work_breaks(db, lessons, events, executor_id, executor_user.id)
