from datetime import datetime, date, time, timedelta

from sqlalchemy.orm import Session

from src.models import Event, User, RecurrentEvent, EventBreak, Base, Executor


class Repository:
    db: Session
    type_model: type[Base]

    def __init__(self, db: Session, type_model: type[Base]):
        self.db = db
        self.type_model = type_model

    def get(self, ident):
        return self.db.get(self.type_model, ident=ident)

    def all(self):
        return self.db.query(self.type_model).all()

    def delete(self, ident):
        obj = self.get(ident)
        if obj is None:
            return True
        self.db.delete(obj)
        return False


class UserRepo(Repository):
    def __init__(self, db: Session):
        super().__init__(db, User)

    def new(self, name: str, role: str, is_exec: bool = False):
        user = User(name=name, role=role)
        if is_exec:
            executor = Executor()
            self.db.add(executor)
            user.executor_id = executor
        self.db.add(user)
        return user


class ExecutorRepo(Repository):
    def __init__(self, db: Session):
        super().__init__(db, Executor)


class EventRepo(Repository):
    def __init__(self, db: Session):
        super().__init__(db, Event)

    def new(self, user: User, executor: Executor, event_type: str, start_time: time, end_time: time, day: date):
        event = Event(
            user_id=user.id,
            executor_id=executor.id,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            date=day,
            weekday=day.weekday(),
        )
        self.db.add(event)
        return event


class RecurrentEventRepo(Repository):
    def __init__(self, db: Session):
        super().__init__(db, RecurrentEvent)

    def new(
        self,
        user: User,
        executor: Executor,
        event_type: str,
        start_time: time,
        end_time: time,
        day: date,
        interval: timedelta,
        start: datetime,
        end: datetime | None = None
    ):
        event = EventRepo(self.db).new(user, executor, event_type, start_time, end_time, day)
        recurrent_event = RecurrentEvent(
            user_id=user.id,
            event_id=event.id,
            event=event,
            executor_id=executor.id,
            interval=int(interval.total_seconds()),
            start=start,
            end=end,
        )
        self.db.add(recurrent_event)
        return recurrent_event


class EventBreakRepo(Repository):
    def __init__(self, db: Session):
        super().__init__(db, EventBreak)

    def new(self, user: User, break_type: str, start: datetime, end: datetime):
        event_break = EventBreak(user_id=user.id, break_type=break_type, start=start, end=end)
        self.db.add(event_break)
        return event_break
