from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src2.models import Base, Teacher
from src2.models.mixins import WeekdayMixin


class Weekend(WeekdayMixin, Base):
    __tablename__ = "weekend"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="weekends")
