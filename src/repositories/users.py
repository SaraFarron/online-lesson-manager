from models import Teacher, User
from repositories import Repository


class UserRepo(Repository):
    def new(self, full_name: str, telegram_id: int, teacher: Teacher, username: str) -> None:
        """Add new entry of model to the database."""
        user = User(
            name=full_name,
            telegram_id=telegram_id,
            teacher=teacher,
            telegram_username=username,
        )
        self.session.add(user)


class TeacherRepo(Repository):
    def new(self, full_name: str, telegram_id: int) -> None:
        """Add new entry of model to the database."""
        teacher = Teacher(name=full_name, telegram_id=telegram_id)
        self.session.add(teacher)
