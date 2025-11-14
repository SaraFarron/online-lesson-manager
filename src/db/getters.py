from datetime import date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.schemas import CancelledRecurrentEventSchema, EventSchema, ExecutorSettingsSchema, RecurrentEventSchema


def get_exec_work_hours_by_user_id(db: Session, user_id: int) -> ExecutorSettingsSchema | None:
    result = db.execute(text(
        """
            SELECT e.work_start as work_start, e.work_end as work_end, id
            FROM executors AS e
            JOIN users AS u ON e.id = u.executor_id
            WHERE u.id = :user_id
        """),
        {"user_id": user_id},
    ).first()
    if result:
        return ExecutorSettingsSchema.from_row(result)
    return None


def get_events_for_day(db: Session, executor_id: int, day: date):
    day_start = datetime.combine(day, time(hour=0, minute=0))
    day_end = datetime.combine(day, time(hour=23, minute=59))
    result = db.execute(text(
        """
            select * from events
            where executor_id = :executor_id and start >= :day_start and end <= :day_end and cancelled is false
            order by start
        """),
        {"executor_id": executor_id, "day_start": day_start, "day_end": day_end + timedelta(seconds=1)},
    ).all()
    return [EventSchema.from_row(row) for row in result]


def get_recurrent_events(db: Session, executor_id: int):
    now = datetime.now()
    result = db.execute(
        text("""
            select * from recurrent_events
            where executor_id = :executor_id and start >= :now
            order by start
        """),
        {"executor_id": executor_id, "now": now},
    ).all()
    return [RecurrentEventSchema.from_row(re) for re in result]


def get_cancels(db: Session, event_ids: list[int]):
    if not event_ids:
        return []
    result = db.execute(
        text("""
            select * from event_breaks
            where event_id in :event_id
            order by start
        """),
        {"event_ids": event_ids},
    ).all()
    return [
        CancelledRecurrentEventSchema.from_row(row) for row in result
    ]
