import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.auth.schemas import (
    StudentProfileCreate,
    StudentProfilePublic,
    TeacherProfileCreate,
    TeacherProfilePublic,
    UserCreate,
)
from backend.auth.utils import hash_password, string_to_time, verify_password


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .options(
            selectinload(User.student_profile),
            selectinload(User.teacher_profile),
        )
    )
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_users(
    session: AsyncSession, *, skip: int = 0, limit: int = 100
) -> tuple[list[User], int]:
    total_result = await session.execute(select(func.count()).select_from(User))
    total = total_result.scalar_one()
    users_result = await session.execute(
        select(User).order_by(User.created_at.desc()).offset(skip).limit(limit)
    )
    return list(users_result.scalars().all()), total


async def create_user(session: AsyncSession, data: UserCreate) -> User:
    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        is_active=data.is_active,
        is_superuser=data.is_superuser,
        role=data.role.value,
        timezone=data.timezone,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def update_user(session: AsyncSession, user: User, data: dict[str, Any]) -> User:
    if "password" in data and data["password"] is not None:
        data["hashed_password"] = hash_password(data.pop("password"))
    else:
        data.pop("password", None)

    for key, value in data.items():
        if value is not None:
            setattr(user, key, value)

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def delete_user(session: AsyncSession, user: User) -> None:
    await session.delete(user)
    await session.commit()


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    """Return the user if credentials are valid, else None.

    Always runs verify_password even when no user is found to prevent
    timing-based email enumeration.
    """
    user = await get_user_by_email(session, email)
    # Constant-time path: always verify even if user is missing
    dummy_hash = "$argon2id$v=19$m=65536,t=3,p=4$dummy$dummy"
    check_hash = user.hashed_password if user else dummy_hash
    if not verify_password(password, check_hash):
        return None
    return user


async def create_student_profile(
    session: AsyncSession, student_profile_data: StudentProfileCreate, user: User
) -> StudentProfilePublic:
    user.student_profile = StudentProfile(
        student=user,
        notification_lesson=student_profile_data.notification_lesson,
        notification_homework=student_profile_data.notification_homework,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return StudentProfilePublic.model_validate(user.student_profile)


async def create_teacher_profile(
    session: AsyncSession, teacher_profile_data: TeacherProfileCreate, user: User
) -> TeacherProfilePublic:
    user.teacher_profile = TeacherProfile(
        teacher=user,
        code=teacher_profile_data.code,
        work_start=string_to_time(teacher_profile_data.work_start) if teacher_profile_data.work_start else None,
        work_end=string_to_time(teacher_profile_data.work_end) if teacher_profile_data.work_end else None,
        lesson_length=teacher_profile_data.lesson_length,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return TeacherProfilePublic.model_validate(user.teacher_profile)

