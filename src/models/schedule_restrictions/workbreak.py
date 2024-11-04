from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models import Base, Teacher
from models.mixins import BordersMixin, WeekdayMixin


class WorkBreak(WeekdayMixin, BordersMixin, Base):
    __tablename__ = "work_break"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teacher.id"))
    teacher: Mapped[Teacher] = relationship(back_populates="breaks")
