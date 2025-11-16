from datetime import date, datetime, time, timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from db.schemas import CancelledRecurrentEventSchema, EventSchema, ExecutorSettingsSchema, RecurrentEventSchema
from src.core.config import DB_DATETIME
from src.db.models import RecurrentEvent


def get_exec_work_hours_by_user_id(db: Session, user_id: int) -> ExecutorSettingsSchema | None:
    result = db.execute(text(
        """
            select distinct executor_id as id, event_type, start, end from recurrent_events
            where event_type in (:start_type, :end_type) and user_id = (
                SELECT u2.id
                FROM users AS u1
                JOIN executors AS e ON u1.executor_id = e.id
                JOIN users AS u2 ON e.telegram_id = u2.telegram_id
                WHERE u1.id = :user_id
            )
        """),
        {
            "user_id": user_id,
            "start_type": RecurrentEvent.EventTypes.WORK_START,
            "end_type": RecurrentEvent.EventTypes.WORK_END,
        },
    ).all()
    assert len(result) == 2, "Executor must have both WORK_START and WORK_END recurrent events"
    for row in result:
        if row.event_type == RecurrentEvent.EventTypes.WORK_START:
            work_start = datetime.strptime(row.start, DB_DATETIME).time()
        elif row.event_type == RecurrentEvent.EventTypes.WORK_END:
            work_end = datetime.strptime(row.end, DB_DATETIME).time()

    return ExecutorSettingsSchema(id=result[0].id, work_start=work_start, work_end=work_end)


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
            where event_id in :event_ids
            order by start
        """).bindparams(bindparam("event_ids", expanding=True)),
        {"event_ids": event_ids},
    ).all()
    return [
        CancelledRecurrentEventSchema.from_row(row) for row in result
    ]


def get_executor_settings_by_id(db: Session, executor_id: int) -> ExecutorSettingsSchema | None:
    result = db.execute(
        text("""
            select distinct executor_id as id, event_type, start, end from recurrent_events
            where event_type in (:start_type, :end_type) and executor_id = :executor_id
        """),
        {
            "executor_id": executor_id,
            "start_type": RecurrentEvent.EventTypes.WORK_START,
            "end_type": RecurrentEvent.EventTypes.WORK_END,
        },
    ).all()
    assert len(result) == 2, "Executor must have both WORK_START and WORK_END recurrent events"
    for row in result:
        if row.event_type == RecurrentEvent.EventTypes.WORK_START:
            work_start = datetime.strptime(row.start, DB_DATETIME).time()
        elif row.event_type == RecurrentEvent.EventTypes.WORK_END:
            work_end = datetime.strptime(row.end, DB_DATETIME).time()

    return ExecutorSettingsSchema(id=result[0].id, work_start=work_start, work_end=work_end)
