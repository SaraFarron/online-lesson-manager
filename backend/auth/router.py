import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from backend.auth import service
from backend.auth.constants import Roles
from backend.auth.dependencies import CurrentUser, SessionDep, SuperUser
from backend.auth.exceptions import EmailAlreadyExists, UserNotFound
from backend.auth.schemas import (
    StudentProfileCreate,
    StudentProfilePublic,
    StudentProfileUpdate,
    TeacherProfileCreate,
    TeacherProfilePublic,
    TeacherProfileUpdate,
    Token,
    UserCreate,
    UserList,
    UserPublic,
    UserUpdate,
    UserUpdateMe,
)
from backend.auth.utils import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Authentication ────────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=Token,
    summary="OAuth2 password login — returns a JWT access token",
)
async def login(session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]) -> Any:
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
async def register(session: SessionDep, data: UserCreate) -> Any:
    if await service.get_user_by_email(session, data.email):
        raise EmailAlreadyExists()
    # Prevent self-elevating to superuser via the public endpoint
    data.is_superuser = False
    return await service.create_user(session, data)


# ── Current user (self-service) ───────────────────────────────────────────────


@router.get(
    "/me",
    response_model=UserPublic,
    summary="Get the currently authenticated user",
)
async def read_me(current_user: CurrentUser) -> Any:
    return current_user


@router.patch(
    "/me",
    response_model=UserPublic,
    summary="Update the currently authenticated user's profile",
)
async def update_me(session: SessionDep, current_user: CurrentUser, data: UserUpdateMe) -> Any:
    if data.email and data.email != current_user.email:
        if await service.get_user_by_email(session, data.email):
            raise EmailAlreadyExists()
    return await service.update_user(
        session, current_user, data.model_dump(exclude_unset=True)
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
    return UserList(items=users, total=total)


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
async def update_user_admin(session: SessionDep, _: SuperUser, user_id: uuid.UUID, data: UserUpdate) -> Any:
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
async def delete_user_admin(session: SessionDep, _: SuperUser, user_id: uuid.UUID) -> None:
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
    _: CurrentUser,
    user_id: uuid.UUID,
    data: StudentProfileCreate,
) -> Any:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
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
    return await service.create_student_profile(session, student_profile_data=data, user=user)


@router.get(
    "/users/{user_id}/student-profile",
    response_model=StudentProfilePublic,
)
async def get_student_profile(
    session: SessionDep,
    _: CurrentUser,
    user_id: uuid.UUID,
) -> Any:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
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
    _: CurrentUser,
    user_id: uuid.UUID,
    data: StudentProfileUpdate,
) -> Any:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
    if not user.student_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student profile does not exist for this user",
        )
    return await service.update_student_profile(session, student_profile_data=data, user=user)


@router.post(
    "/users/{user_id}/teacher-profile",
    response_model=TeacherProfilePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_teacher_profile(
    session: SessionDep,
    _: CurrentUser,
    user_id: uuid.UUID,
    data: TeacherProfileCreate,
) -> Any:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
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
    return await service.create_teacher_profile(session, teacher_profile_data=data, user=user)


@router.get(
    "/users/{user_id}/teacher-profile",
    response_model=TeacherProfilePublic,
)
async def get_teacher_profile(
    session: SessionDep,
    _: CurrentUser,
    user_id: uuid.UUID,
) -> Any:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
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
    _: CurrentUser,
    user_id: uuid.UUID,
    data: TeacherProfileUpdate,
) -> Any:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
    if not user.teacher_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher profile does not exist for this user",
        )
    return await service.update_teacher_profile(session, teacher_profile_data=data, user=user)
