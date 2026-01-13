from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Lesson(Base):
    """Lesson model representing a lesson in the system."""

    __tablename__ = "lessons"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    is_published: Mapped[bool] = mapped_column(default=False)

    def __repr__(self) -> str:
        return f"<Lesson(id={self.id}, title='{self.title}')>"
