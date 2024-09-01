from __future__ import annotations

from sqlalchemy import Date, ForeignKey, String, Time
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user_account"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    lessons: Mapped[list[Lesson]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        """String model represetation."""
        return f"User(id={self.id!r}, name={self.name!r})"


class Lesson(Base):
    __tablename__ = "lesson"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[Date] = mapped_column(Date)
    time: Mapped[Time] = mapped_column(Time)
    end_time: Mapped[Time] = mapped_column(Time)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="lessons")
    # statuses: upcoming, canceled, completed
    status: Mapped[str] = mapped_column(String(10), default="upcoming")

    def __repr__(self) -> str:
        """String model represetation."""
        return f"Lesson(id={self.id!r}, date={self.date!r}, time={self.time!r}, user_id={self.user_id!r})"
