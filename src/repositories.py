from sqlalchemy.orm import Session
from src.models import User, EventHistory, Executor
from src.errors import PermissionDeniedError


class UserRepo:
    def __init__(self, db: Session):
        self.db = db

    @property
    def roles(self):
        return User.Roles

    def get_by_telegram_id(self, telegram_id: int):
        """Retrieve a user by telegram id."""
        return self.db.query(User).filter(User.telegram_id == telegram_id).first()

    def register(self, tg_id: int, tg_full_name: str, tg_username: str, role: str, code: str):
        """Register a user."""
        event_log = EventHistory(
            author=tg_username,
            scene="start",
            event_type="register",
            event_value=f"tg_id: {tg_id}, tg_full_name: {tg_full_name}, tg_username: {tg_username}, role: {role}, executor: {code}",
        )

        executor = self.db.query(Executor).filter(code=code).first()
        if executor is None:
            raise PermissionDeniedError

        user = User(
            telegram_id=tg_id,
            username=tg_username,
            full_name=tg_full_name,
            role=role,
            executor_id=executor.id
        )
        self.db.add_all([user, event_log])
        self.db.commit()
