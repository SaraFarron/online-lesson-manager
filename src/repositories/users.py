from __future__ import annotations

from sqlalchemy.orm import Session

from models import Teacher, User
from repositories import Repository


class UserRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize user repository class."""
        super().__init__(User, session)

    def new(self, full_name: str, telegram_id: int, teacher: Teacher, username: str) -> None:
        """Add new entry of model to the database."""
        user = User(
            name=full_name,
            telegram_id=telegram_id,
            teacher=teacher,
            telegram_username=username,
        )
        self.session.add(user)

    def get_by_telegram_id(self, telegram_id: int) -> User | None:
        """Get user by telegram id."""
        return self.session.query(User).filter(User.telegram_id == telegram_id).first()


class TeacherRepo(Repository):
    def __init__(self, session: Session) -> None:
        """Initialize teacher repository class."""
        super().__init__(Teacher, session)

    def new(self, full_name: str, telegram_id: int):
        """Add new entry of model to the database."""
        teacher = Teacher(name=full_name, telegram_id=telegram_id)
        self.session.add(teacher)
        return teacher

    def register(self, full_name: str, telegram_id: int, username: str):
        """Creates teacher and user in db."""
        teacher = self.new(full_name, telegram_id)
        UserRepo(self.session).new(full_name, telegram_id, teacher, username)
        return teacher
