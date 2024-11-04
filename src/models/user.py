import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from models import Base
from structures.role import Role


class User(Base):
    """User model."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(
        sa.BigInteger,
        unique=True,
        primary_key=True,
        autoincrement=False,
        nullable=False,
    )
    """ Telegram user id """
    user_name: Mapped[str] = mapped_column(
        sa.Text,
        unique=False,
        nullable=True,
    )
    """ Telegram user name """
    first_name: Mapped[str] = mapped_column(
        sa.Text,
        unique=False,
        nullable=True,
    )
    """ Telegram profile first name """
    second_name: Mapped[str] = mapped_column(
        sa.Text,
        unique=False,
        nullable=True,
    )
    """ Telegram profile second name """
    is_premium: Mapped[bool] = mapped_column(
        sa.Boolean,
        unique=False,
        nullable=False,
    )
    """ Telegram user premium status """
    role: Mapped[Role] = mapped_column(
        sa.Enum(Role),
        default=Role.STUDENT,
    )
    """ User's role """
    is_banned: Mapped[bool] = mapped_column(
        sa.Boolean,
        default=False,
    )
    """ User's ban status """
