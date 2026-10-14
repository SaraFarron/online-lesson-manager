from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import service as auth_service
from backend.auth.exceptions import InactiveUser
from backend.auth.models import User
from backend.telegram.schemas import (
    StudentRegistration,
    TeacherRegistration,
    TelegramRegistration,
    TelegramSender,
)


async def create_session(session: AsyncSession, sender: TelegramSender) -> User:
    user = await session.scalar(
        select(User).where(User.telegram_user_id == sender.telegram_user_id)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="telegram_identity_not_found",
        )
    if not user.is_active:
        raise InactiveUser()
    if (
        sender.telegram_private_chat_id is not None
        and sender.telegram_private_chat_id != user.telegram_private_chat_id
    ):
        user.telegram_private_chat_id = sender.telegram_private_chat_id
        await session.commit()
        await session.refresh(user)
    return user


def already_registered() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="telegram_already_registered",
    )


def is_telegram_identity_conflict(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return (
        getattr(diagnostic, "constraint_name", None) == "user_telegram_user_id_key"
        or str(error.orig) == "UNIQUE constraint failed: user.telegram_user_id"
    )


async def register(session: AsyncSession, data: TelegramRegistration) -> User:
    # Bot-service authentication does no SQL; this service owns the transaction.
    try:
        async with session.begin():
            existing = await session.scalar(
                select(User.id).where(User.telegram_user_id == data.telegram_user_id)
            )
            if existing is not None:
                raise already_registered()
            teacher = (
                await auth_service.resolve_teacher_code(session, data.teacher_code)
                if isinstance(data, StudentRegistration)
                else None
            )
            user = await auth_service.add_user(
                session,
                User(
                    role=data.role,
                    timezone=data.timezone,
                    full_name=data.full_name,
                    notification_lessons_today=data.notification_lessons_today,
                    telegram_user_id=data.telegram_user_id,
                    telegram_private_chat_id=data.telegram_private_chat_id,
                    teacher_id=teacher.id if teacher is not None else None,
                    is_active=True,
                    is_superuser=False,
                ),
            )
            if isinstance(data, TeacherRegistration):
                await auth_service.add_teacher_profile(session, data.profile, user)
            else:
                await auth_service.add_student_profile(session, data.profile, user)
    except IntegrityError as exc:
        if is_telegram_identity_conflict(exc):
            raise already_registered() from exc
        raise
    return user
