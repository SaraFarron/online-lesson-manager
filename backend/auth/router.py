import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm

from backend.auth import service
from backend.auth.constants import Roles
from backend.auth.dependencies import (
    CurrentUser,
    OwnedProfileUser,
    SessionDep,
    SuperUser,
)
from backend.auth.exceptions import (
    EmailAlreadyExists,
    InsufficientPermissions,
    UserNotFound,
)
from backend.auth.schemas import (
    StudentJoinTeacher,
    StudentPickerItem,
    StudentPickerPage,
    StudentProfileCreate,
    StudentProfilePublic,
    StudentProfileUpdate,
    TeacherBookingContext,
    TeacherProfileCreate,
    TeacherProfilePublic,
    TeacherProfileUpdate,
    Token,
    UserCreate,
    UserList,
    UserPublic,
    UserRegister,
    UserUpdate,
    UserUpdateMe,
)
from backend.auth.utils import create_access_token
from backend.events.booking_policy import get_teacher_preferences
from backend.exceptions import BadRequest, Conflict

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={
        401: {"description": "Missing or invalid user authentication"},
        403: {"description": "Profile access requires ownership or superuser"},
        404: {"description": "Account, profile or invitation not found"},
        409: {"description": "Email or invitation code already in use"},
        422: {"description": "Invalid preferences or forbidden input fields"},
    },
)


# ── Authentication ────────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=Token,
    summary="OAuth2 password login — returns a JWT access token",
)
async def login(
    session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Any:
    user = await service.authenticate(session, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user",
        )
    return Token(access_token=create_access_token(str(user.id)))


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(session: SessionDep, data: UserRegister) -> Any:
    if await service.get_user_by_email(session, data.email):
        raise EmailAlreadyExists()
    return await service.create_user(session, UserCreate(**data.model_dump()))


# ── Current user (self-service) ───────────────────────────────────────────────


@router.get(
    "/me",
    response_model=UserPublic,
    summary="Get the currently authenticated user",
)
async def read_me(current_user: CurrentUser) -> Any:
    return current_user


@router.get(
    "/me/teacher-context",
    response_model=TeacherBookingContext,
    summary="Read linked teacher booking defaults as a student",
)
async def read_teacher_context(
    session: SessionDep, current_user: CurrentUser
) -> TeacherBookingContext:
    if current_user.role != Roles.STUDENT:
        raise InsufficientPermissions()
    if current_user.teacher_id is None:
        raise BadRequest("Student is not linked to a teacher")

    teacher = await service.get_user_by_id(session, current_user.teacher_id)
    if teacher is None or teacher.role != Roles.TEACHER or not teacher.is_active:
        raise Conflict("Linked teacher cannot provide booking context")

    preferences, timezone = await get_teacher_preferences(
        session, current_user.teacher_id
    )
    return TeacherBookingContext(
        teacher_id=teacher.id,
        timezone=timezone,
        work_start=(
            preferences.work_start.strftime("%H:%M")
            if preferences.work_start is not None
            else None
        ),
        work_end=(
            preferences.work_end.strftime("%H:%M")
            if preferences.work_end is not None
            else None
        ),
        working_days=list(preferences.working_days),
        lesson_length=preferences.lesson_length,
    )


@router.patch(
    "/me",
    response_model=UserPublic,
    summary="Update own profile, timezone and local daily-digest preference",
    description="Omitted fields are unchanged; null disables the digest. Privilege fields are forbidden.",
)
async def update_me(
    session: SessionDep, current_user: CurrentUser, data: UserUpdateMe
) -> Any:
    if data.email and data.email != current_user.email:
        if await service.get_user_by_email(session, data.email):
            raise EmailAlreadyExists()
    return await service.update_user(
        session, current_user, data.model_dump(exclude_unset=True)
    )


@router.post(
    "/me/teacher",
    response_model=UserPublic,
    summary="Link the currently authenticated student to a teacher by code",
)
async def join_teacher(
    session: SessionDep, current_user: CurrentUser, data: StudentJoinTeacher
) -> Any:
    return await service.link_student_to_teacher(
        session, current_user, data.teacher_code
    )


@router.get(
    "/teacher/students",
    response_model=StudentPickerPage,
    summary="List the authenticated teacher's active students for selection",
)
async def list_teacher_students(
    session: SessionDep,
    current_user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> StudentPickerPage:
    if current_user.role != Roles.TEACHER:
        raise InsufficientPermissions()

    students, total = await service.get_teacher_students(
        session, current_user.id, skip=skip, limit=limit
    )
    return StudentPickerPage(
        items=[
            StudentPickerItem(
                id=student.id,
                display_label=student.full_name or f"Student {str(student.id)[:8]}",
            )
            for student in students
        ],
        total=total,
    )


# ── Admin — user management (superuser only) ──────────────────────────────────


@router.get(
    "/users",
    response_model=UserList,
    summary="[Admin] List all users",
)
async def list_users(
    session: SessionDep,
    _: SuperUser,
    skip: int = 0,
    limit: int = 100,
) -> Any:
    users, total = await service.get_users(session, skip=skip, limit=limit)
    return {"items": users, "total": total}


@router.post(
    "/users",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create a user with arbitrary privileges",
)
async def create_user_admin(
    session: SessionDep,
    _: SuperUser,
    data: UserCreate,
) -> Any:
    if await service.get_user_by_email(session, data.email):
        raise EmailAlreadyExists()
    return await service.create_user(session, data)


@router.get(
    "/users/{user_id}",
    response_model=UserPublic,
    summary="[Admin] Get a user by ID",
)
async def get_user_admin(session: SessionDep, _: SuperUser, user_id: uuid.UUID) -> Any:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
    return user


@router.patch(
    "/users/{user_id}",
    response_model=UserPublic,
    summary="[Admin] Update any user",
)
async def update_user_admin(
    session: SessionDep, _: SuperUser, user_id: uuid.UUID, data: UserUpdate
) -> Any:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
    if data.email and data.email != user.email:
        if await service.get_user_by_email(session, data.email):
            raise EmailAlreadyExists()
    return await service.update_user(session, user, data.model_dump(exclude_unset=True))


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Admin] Delete a user",
)
async def delete_user_admin(
    session: SessionDep, _: SuperUser, user_id: uuid.UUID
) -> None:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
    await service.delete_user(session, user)


@router.post(
    "/users/{user_id}/student-profile",
    response_model=StudentProfilePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_student_profile(
    session: SessionDep,
    user: OwnedProfileUser,
    data: StudentProfileCreate,
) -> Any:
    if user.student_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Student profile already exists for this user",
        )
    if user.role != Roles.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a student",
        )
    return await service.create_student_profile(
        session, student_profile_data=data, user=user
    )


@router.get(
    "/users/{user_id}/student-profile",
    response_model=StudentProfilePublic,
)
async def get_student_profile(
    user: OwnedProfileUser,
) -> Any:
    if not user.student_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student profile does not exist for this user",
        )
    return user.student_profile


@router.put(
    "/users/{user_id}/student-profile",
    response_model=StudentProfilePublic,
)
async def update_student_profile(
    session: SessionDep,
    user: OwnedProfileUser,
    data: StudentProfileUpdate,
) -> Any:
    if not user.student_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student profile does not exist for this user",
        )
    return await service.update_student_profile(
        session, student_profile_data=data, user=user
    )


@router.post(
    "/users/{user_id}/teacher-profile",
    response_model=TeacherProfilePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_teacher_profile(
    session: SessionDep,
    user: OwnedProfileUser,
    data: TeacherProfileCreate,
) -> Any:
    if user.teacher_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Teacher profile already exists for this user",
        )
    if user.role != Roles.TEACHER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a teacher",
        )
    return await service.create_teacher_profile(
        session, teacher_profile_data=data, user=user
    )


@router.get(
    "/users/{user_id}/teacher-profile",
    response_model=TeacherProfilePublic,
)
async def get_teacher_profile(
    user: OwnedProfileUser,
) -> Any:
    if not user.teacher_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher profile does not exist for this user",
        )
    return user.teacher_profile


@router.put(
    "/users/{user_id}/teacher-profile",
    response_model=TeacherProfilePublic,
)
async def update_teacher_profile(
    session: SessionDep,
    user: OwnedProfileUser,
    data: TeacherProfileUpdate,
) -> Any:
    if not user.teacher_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher profile does not exist for this user",
        )
    return await service.update_teacher_profile(
        session, teacher_profile_data=data, user=user
    )
