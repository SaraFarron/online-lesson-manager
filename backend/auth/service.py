import re
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from backend.activity import service as activity_service
from backend.activity.constants import StudentActionType
from backend.auth.constants import (
    STUDENT_NAME_UNAVAILABLE_LABEL,
    STUDENT_USERNAME_UNAVAILABLE_LABEL,
    Roles,
)
from backend.auth.exceptions import (
    NotAStudent,
    StudentAlreadyLinked,
    TeacherCodeAlreadyExists,
    TeacherCodeGenerationFailed,
    TeacherCodeNotFound,
)
from backend.auth.models import (
    StudentProfile,
    StudentTeacherRelationship,
    TeacherProfile,
    User,
)
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
from backend.exceptions import Conflict
from backend.homework.models import Homework


@dataclass(frozen=True)
class TeacherStudentProfileData:
    relationship_id: uuid.UUID
    student_id: uuid.UUID
    display_name: str
    username_display: str
    linked_at: datetime | None
    removed_at: datetime | None
    homework_assigned: int
    homework_completed: int
    homework_incomplete: int
    homework_completed_by_student: int
    homework_completed_by_teacher: int


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
    try:
        relationship = await record_student_teacher_link(session, student, teacher.id)
        await activity_service.record_student_action(
            session,
            student,
            StudentActionType.TEACHER_LINKED,
            relationship=relationship,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if is_active_relationship_conflict(exc):
            raise StudentAlreadyLinked() from exc
        raise
    await session.refresh(student)
    return student


async def record_student_teacher_link(
    session: AsyncSession, student: User, teacher_id: uuid.UUID
) -> StudentTeacherRelationship:
    relationship = StudentTeacherRelationship(
        student_id=student.id,
        teacher_id=teacher_id,
        linked_at=datetime.now(UTC),
    )
    session.add(relationship)
    await session.flush()
    return relationship


def is_active_relationship_conflict(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return (
        getattr(diagnostic, "constraint_name", None)
        == "student_teacher_relationship_one_active_per_student_idx"
        or str(error.orig)
        == "UNIQUE constraint failed: student_teacher_relationship.student_id"
    )


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


async def get_teacher_students(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[User], int]:
    filters = (
        User.teacher_id == teacher_id,
        User.role == Roles.STUDENT,
        User.is_active.is_(True),
    )
    total_result = await session.execute(
        select(func.count()).select_from(User).where(*filters)
    )
    total = total_result.scalar_one()
    students_result = await session.execute(
        select(User)
        .where(*filters)
        .order_by(User.full_name, User.id)
        .offset(skip)
        .limit(limit)
    )
    return list(students_result.scalars().all()), total


def _teacher_student_profile_query(teacher_id: uuid.UUID) -> Select[Any]:
    homework_counts = (
        select(
            Homework.student_id.label("student_id"),
            func.count(Homework.id).label("assigned"),
            func.sum(case((Homework.status == "COMPLETE", 1), else_=0)).label(
                "completed"
            ),
            func.sum(
                case(
                    (
                        (Homework.status == "COMPLETE")
                        & (Homework.completion_source == "STUDENT_SUBMISSION"),
                        1,
                    ),
                    else_=0,
                )
            ).label("completed_by_student"),
            func.sum(
                case(
                    (
                        (Homework.status == "COMPLETE")
                        & (Homework.completion_source == "TEACHER"),
                        1,
                    ),
                    else_=0,
                )
            ).label("completed_by_teacher"),
        )
        .where(
            Homework.teacher_id == teacher_id,
            Homework.deleted_at.is_(None),
            Homework.cleanup_pending.is_(False),
        )
        .group_by(Homework.student_id)
        .subquery()
    )
    return (
        select(
            StudentTeacherRelationship.id.label("relationship_id"),
            StudentTeacherRelationship.student_id.label("student_id"),
            User.full_name.label("full_name"),
            User.telegram_username.label("telegram_username"),
            StudentTeacherRelationship.linked_at.label("linked_at"),
            StudentTeacherRelationship.removed_at.label("removed_at"),
            func.coalesce(homework_counts.c.assigned, 0).label("homework_assigned"),
            func.coalesce(homework_counts.c.completed, 0).label("homework_completed"),
            func.coalesce(homework_counts.c.completed_by_student, 0).label(
                "homework_completed_by_student"
            ),
            func.coalesce(homework_counts.c.completed_by_teacher, 0).label(
                "homework_completed_by_teacher"
            ),
            (
                func.coalesce(homework_counts.c.assigned, 0)
                - func.coalesce(homework_counts.c.completed, 0)
            ).label("homework_incomplete"),
        )
        .join(User, User.id == StudentTeacherRelationship.student_id)
        .outerjoin(homework_counts, homework_counts.c.student_id == User.id)
        .where(StudentTeacherRelationship.teacher_id == teacher_id)
    )


def _teacher_student_profile_data(row: Any) -> TeacherStudentProfileData:
    display_name = row.full_name.strip() if row.full_name else ""
    username = row.telegram_username.strip() if row.telegram_username else ""
    return TeacherStudentProfileData(
        relationship_id=row.relationship_id,
        student_id=row.student_id,
        display_name=display_name or STUDENT_NAME_UNAVAILABLE_LABEL,
        username_display=username or STUDENT_USERNAME_UNAVAILABLE_LABEL,
        linked_at=row.linked_at,
        removed_at=row.removed_at,
        homework_assigned=int(row.homework_assigned),
        homework_completed=int(row.homework_completed),
        homework_incomplete=int(row.homework_incomplete),
        homework_completed_by_student=int(row.homework_completed_by_student),
        homework_completed_by_teacher=int(row.homework_completed_by_teacher),
    )


async def get_active_teacher_student_profiles(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[TeacherStudentProfileData], int]:
    filters = (
        User.teacher_id == teacher_id,
        User.role == Roles.STUDENT,
        User.is_active.is_(True),
        StudentTeacherRelationship.removed_at.is_(None),
    )
    total = await session.scalar(
        select(func.count(StudentTeacherRelationship.id))
        .select_from(StudentTeacherRelationship)
        .join(User, User.id == StudentTeacherRelationship.student_id)
        .where(StudentTeacherRelationship.teacher_id == teacher_id, *filters)
    )
    rows = await session.execute(
        _teacher_student_profile_query(teacher_id)
        .where(*filters)
        .order_by(User.full_name, User.id, StudentTeacherRelationship.id)
        .offset(skip)
        .limit(limit)
    )
    return [_teacher_student_profile_data(row) for row in rows], int(total or 0)


async def get_active_teacher_student_profile(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
) -> TeacherStudentProfileData | None:
    row = (
        await session.execute(
            _teacher_student_profile_query(teacher_id)
            .where(
                StudentTeacherRelationship.student_id == student_id,
                StudentTeacherRelationship.removed_at.is_(None),
                User.teacher_id == teacher_id,
                User.role == Roles.STUDENT,
                User.is_active.is_(True),
            )
            .limit(1)
        )
    ).first()
    return _teacher_student_profile_data(row) if row is not None else None


async def get_former_teacher_student_profiles(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[TeacherStudentProfileData], int]:
    filters = (
        StudentTeacherRelationship.removed_at.is_not(None),
        User.role == Roles.STUDENT,
    )
    total = await session.scalar(
        select(func.count(StudentTeacherRelationship.id))
        .select_from(StudentTeacherRelationship)
        .join(User, User.id == StudentTeacherRelationship.student_id)
        .where(StudentTeacherRelationship.teacher_id == teacher_id, *filters)
    )
    rows = await session.execute(
        _teacher_student_profile_query(teacher_id)
        .where(*filters)
        .order_by(
            StudentTeacherRelationship.removed_at.desc(),
            User.full_name,
            User.id,
            StudentTeacherRelationship.id,
        )
        .offset(skip)
        .limit(limit)
    )
    return [_teacher_student_profile_data(row) for row in rows], int(total or 0)


async def get_former_teacher_student_profile(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    relationship_id: uuid.UUID,
) -> TeacherStudentProfileData | None:
    row = (
        await session.execute(
            _teacher_student_profile_query(teacher_id)
            .where(
                StudentTeacherRelationship.id == relationship_id,
                StudentTeacherRelationship.removed_at.is_not(None),
                User.role == Roles.STUDENT,
            )
            .limit(1)
        )
    ).first()
    return _teacher_student_profile_data(row) if row is not None else None


async def get_teacher_student_roster(
    session: AsyncSession, teacher_id: uuid.UUID
) -> list[User]:
    page_size = 100
    skip = 0
    expected_total: int | None = None
    students: list[User] = []
    while expected_total is None or skip < expected_total:
        page, total = await get_teacher_students(
            session, teacher_id, skip=skip, limit=page_size
        )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise Conflict("broadcast_roster_changed")
        if not page and skip < total:
            raise Conflict("broadcast_roster_changed")
        students.extend(page)
        skip += len(page)

    if len(students) != expected_total or len({user.id for user in students}) != skip:
        raise Conflict("broadcast_roster_changed")
    return students


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


async def update_user(
    session: AsyncSession,
    user: User,
    data: dict[str, Any],
    *,
    actor: User | None = None,
) -> User:
    changed = any(
        value != getattr(user, key)
        for key, value in data.items()
        if key != "password" and hasattr(user, key)
    )
    if "password" in data and data["password"] is not None:
        if user.telegram_user_id is not None and user.hashed_password is None:
            raise HTTPException(
                status_code=400,
                detail="Password credentials are not supported for Telegram-only accounts",
            )
        if actor is not None and actor.id == user.id and user.role == Roles.STUDENT:
            changed = (
                changed
                or user.hashed_password is None
                or not verify_password(data["password"], user.hashed_password)
            )
        data["hashed_password"] = hash_password(data.pop("password"))
    else:
        data.pop("password", None)

    schedule_settings_changed = (
        "notification_lessons_today" in data
        and data["notification_lessons_today"] != user.notification_lessons_today
    )
    timezone_changed = "timezone" in data and data["timezone"] != user.timezone
    telegram_route_changed = (
        "telegram_private_chat_id" in data
        and data["telegram_private_chat_id"] is not None
        and data["telegram_private_chat_id"] != user.telegram_private_chat_id
    )
    if schedule_settings_changed or timezone_changed or telegram_route_changed:
        user.notification_schedule_updated_at = datetime.now(UTC)

    for key, value in data.items():
        if value is not None or key in {
            "email",
            "full_name",
            "notification_lessons_today",
        }:
            setattr(user, key, value)

    session.add(user)
    if (
        changed
        and actor is not None
        and actor.id == user.id
        and user.role == Roles.STUDENT
    ):
        await activity_service.record_student_action(
            session, user, StudentActionType.ACCOUNT_PROFILE_UPDATED
        )
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
    session: AsyncSession,
    student_profile_data: StudentProfileCreate,
    user: User,
    *,
    actor: User | None = None,
) -> StudentProfilePublic:
    profile = await add_student_profile(session, student_profile_data, user)
    if actor is not None and actor.id == user.id and user.role == Roles.STUDENT:
        await activity_service.record_student_action(
            session, user, StudentActionType.STUDENT_PROFILE_CREATED
        )
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
    session: AsyncSession,
    student_profile_data: StudentProfileUpdate,
    user: User,
    *,
    actor: User | None = None,
) -> StudentProfilePublic:
    if not user.student_profile:
        raise ValueError("User does not have a student profile")
    updates = student_profile_data.model_dump(exclude_unset=True)
    changed = any(
        value != getattr(user.student_profile, key) for key, value in updates.items()
    )
    if (
        "notification_lesson" in updates
        and updates["notification_lesson"] != user.student_profile.notification_lesson
    ):
        user.student_profile.notification_lesson_updated_at = datetime.now(UTC)
    if (
        "notification_homework" in updates
        and updates["notification_homework"]
        != user.student_profile.notification_homework
    ):
        user.student_profile.notification_homework_updated_at = datetime.now(UTC)
    for key, value in updates.items():
        setattr(user.student_profile, key, value)
    session.add(user)
    if (
        changed
        and actor is not None
        and actor.id == user.id
        and user.role == Roles.STUDENT
    ):
        await activity_service.record_student_action(
            session, user, StudentActionType.STUDENT_SETTINGS_UPDATED
        )
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
    async def update() -> TeacherProfilePublic:
        if not user.teacher_profile:
            raise ValueError("User does not have a teacher profile")
        profile = user.teacher_profile
        await session.refresh(profile)
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
        session.add(profile)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if is_teacher_code_conflict(exc):
                raise TeacherCodeAlreadyExists() from exc
            raise
        await session.refresh(profile)
        return TeacherProfilePublic.model_validate(profile)

    from backend.events.service import run_serialized_schedule_mutation

    return await run_serialized_schedule_mutation(session, user.id, update)
