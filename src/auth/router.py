import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from src.auth import service
from src.auth.dependencies import CurrentUser, SessionDep, SuperUser
from src.auth.exceptions import EmailAlreadyExists, UserNotFound
from src.auth.schemas import (
    Token,
    UserCreate,
    UserList,
    UserPublic,
    UserUpdate,
    UserUpdateMe,
)
from src.auth.utils import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Authentication ────────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=Token,
    summary="OAuth2 password login — returns a JWT access token",
)
async def login(
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
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
async def update_me(
    session: SessionDep,
    current_user: CurrentUser,
    data: UserUpdateMe,
) -> Any:
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
    dependencies=[Depends(SuperUser)],
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
async def get_user_admin(
    session: SessionDep,
    _: SuperUser,
    user_id: uuid.UUID,
) -> Any:
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
    session: SessionDep,
    _: SuperUser,
    user_id: uuid.UUID,
    data: UserUpdate,
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
    session: SessionDep,
    _: SuperUser,
    user_id: uuid.UUID,
) -> None:
    user = await service.get_user_by_id(session, user_id)
    if not user:
        raise UserNotFound()
    await service.delete_user(session, user)
