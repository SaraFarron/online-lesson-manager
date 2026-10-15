import re
import secrets
import string
import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.auth.constants import Roles
from backend.auth.exceptions import (
    NotAStudent,
    StudentAlreadyLinked,
    TeacherCodeAlreadyExists,
    TeacherCodeGenerationFailed,
    TeacherCodeNotFound,
)
from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.auth.schemas import (
    StudentProfileCreate,
    StudentProfilePublic,
    StudentProfileUpdate,
    TeacherPreferences,
    TeacherProfileCreate,
    TeacherProfilePublic,
    TeacherProfileUpdate,
    UserCreate,
)
from backend.auth.utils import (
    hash_password,
    is_teacher_code_conflict,
    string_to_time,
    verify_password,
)

DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$H7HFOyCv3KslcnJb9IzN6w$"
    "QIK1TR87Xy+3SbsJzNUK3BwbAEgQeFmsIj3XLeWmQSw"
)
TEACHER_CODE_PATTERN = re.compile(r"t_[A-Z0-9]{6}")


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


async def link_student_to_teacher(
    session: AsyncSession, student: User, teacher_code: str
) -> User:
    if student.role != Roles.STUDENT:
        raise NotAStudent()
    if student.teacher_id is not None:
        raise StudentAlreadyLinked()

    teacher = await resolve_teacher_code(session, teacher_code)
    student.teacher_id = teacher.id
    session.add(student)
    await session.commit()
    await session.refresh(student)
    return student


async def resolve_teacher_code(session: AsyncSession, teacher_code: str) -> User:
    result = await session.execute(
        select(User)
        .join(TeacherProfile, TeacherProfile.teacher_id == User.id)
        .where(
            TeacherProfile.code == teacher_code,
            User.role == Roles.TEACHER,
            User.is_active.is_(True),
        )
    )
    teacher = result.scalar_one_or_none()
    if teacher is None:
        raise TeacherCodeNotFound()
    return teacher


async def add_user(session: AsyncSession, user: User) -> User:
    session.add(user)
    await session.flush()
    return user


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
    await add_user(session, user)
    await session.commit()
    await session.refresh(user)
    return user


async def update_user(session: AsyncSession, user: User, data: dict[str, Any]) -> User:
    if "password" in data and data["password"] is not None:
        if user.telegram_user_id is not None and user.hashed_password is None:
            raise HTTPException(
                status_code=400,
                detail="Password credentials are not supported for Telegram-only accounts",
            )
        data["hashed_password"] = hash_password(data.pop("password"))
    else:
        data.pop("password", None)

    for key, value in data.items():
        if value is not None or key in {
            "email",
            "full_name",
            "notification_lessons_today",
        }:
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
    check_hash = (
        user.hashed_password
        if user is not None and user.hashed_password is not None
        else DUMMY_PASSWORD_HASH
    )
    valid = verify_password(password, check_hash)
    if not valid or user is None or user.hashed_password is None:
        return None
    return user


async def create_student_profile(
    session: AsyncSession, student_profile_data: StudentProfileCreate, user: User
) -> StudentProfilePublic:
    profile = await add_student_profile(session, student_profile_data, user)
    await session.commit()
    await session.refresh(profile)
    return StudentProfilePublic.model_validate(profile)


async def add_student_profile(
    session: AsyncSession, data: StudentProfileCreate, user: User
) -> StudentProfile:
    profile = StudentProfile(student_id=user.id, **data.model_dump())
    session.add(profile)
    await session.flush()
    return profile


async def update_student_profile(
    session: AsyncSession, student_profile_data: StudentProfileUpdate, user: User
) -> StudentProfilePublic:
    if not user.student_profile:
        raise ValueError("User does not have a student profile")
    for key, value in student_profile_data.model_dump(exclude_unset=True).items():
        setattr(user.student_profile, key, value)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return StudentProfilePublic.model_validate(user.student_profile)


async def create_teacher_profile(
    session: AsyncSession, teacher_profile_data: TeacherProfileCreate, user: User
) -> TeacherProfilePublic:
    profile = await add_teacher_profile(
        session, teacher_profile_data, user, code=teacher_profile_data.code
    )
    await session.commit()
    await session.refresh(profile)
    return TeacherProfilePublic.model_validate(profile)


def generate_teacher_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "t_" + "".join(secrets.choice(alphabet) for _ in range(6))


def is_generated_teacher_code(code: str) -> bool:
    return TEACHER_CODE_PATTERN.fullmatch(code) is not None


async def add_teacher_profile(
    session: AsyncSession,
    data: TeacherPreferences,
    user: User,
    *,
    code: str | None = None,
) -> TeacherProfile:
    values = data.model_dump(exclude={"code"})
    values["work_start"] = string_to_time(data.work_start)
    values["work_end"] = string_to_time(data.work_end)
    for _ in range(5):
        try:
            async with session.begin_nested():
                profile = TeacherProfile(
                    teacher_id=user.id, code=code or generate_teacher_code(), **values
                )
                session.add(profile)
                await session.flush()
            return profile
        except IntegrityError as exc:
            if not is_teacher_code_conflict(exc):
                raise
            if code is not None:
                raise TeacherCodeAlreadyExists() from exc
    raise TeacherCodeGenerationFailed()


async def update_teacher_profile(
    session: AsyncSession, teacher_profile_data: TeacherProfileUpdate, user: User
) -> TeacherProfilePublic:
    if not user.teacher_profile:
        raise ValueError("User does not have a teacher profile")
    profile = user.teacher_profile
    saved = TeacherProfilePublic.model_validate(profile).model_dump(
        mode="json", exclude={"id", "teacher_id"}
    )
    saved.update(teacher_profile_data.model_dump(exclude_unset=True))
    try:
        merged = TeacherProfileCreate.model_validate(saved)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_context=False)) from exc
    for key, value in merged.model_dump().items():
        if key in {"work_start", "work_end"}:
            value = string_to_time(value)
        setattr(profile, key, value)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if is_teacher_code_conflict(exc):
            raise TeacherCodeAlreadyExists() from exc
        raise
    await session.refresh(user)
    return TeacherProfilePublic.model_validate(user.teacher_profile)
