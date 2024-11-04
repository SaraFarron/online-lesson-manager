from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models import Base, User
from models.mixins import BordersMixin, WeekdayMixin


class RestrictedTime(WeekdayMixin, BordersMixin, Base):
    __tablename__ = "restricted_time"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[User] = relationship(back_populates="restricted_times")
    whole_day_restricted: Mapped[bool] = mapped_column(default=False)
