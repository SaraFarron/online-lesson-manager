from __future__ import annotations

from sqlalchemy.orm import Session

from config import config, logs
from logger import logger
from repositories import TeacherRepo, UserRepo


class RegistrationService:
    def __init__(self, session: Session) -> None:
        """Service for handling registration."""
        self.session = session

    def register_user(self, teacher_tg_id: int, telegram_id: int, full_name: str, username: str | None = None):
        """User registration."""
        teacher = TeacherRepo(self.session).get_by_telegram_id(teacher_tg_id)
        if not teacher:
            msg = f"Teacher {teacher_tg_id} not found"
            raise ValueError(msg)
        UserRepo(self.session).new(full_name, telegram_id, teacher, username)

    def register_teacher(self, telegram_id: int, full_name: str, username: str | None = None):
        """Teacher registration."""
        TeacherRepo(self.session).register(full_name, telegram_id, username or full_name)

    def register(self, admin_id: int, telegram_id: int, full_name: str, username: str | None = None):
        """Register user or teacher."""
        if telegram_id in config.ADMINS:
            self.register_teacher(telegram_id, full_name, username)
            logger.info(logs.TEACHER_REGISTERED, full_name)
        else:
            self.register_user(admin_id, telegram_id, full_name, username)
            logger.info(logs.USER_REGISTERED, full_name)
        self.session.commit()
