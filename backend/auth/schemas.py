import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ── User ──────────────────────────────────────────────────────────────────────


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=256)
    is_active: bool = True
    is_superuser: bool = False


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    """Admin: update any user field."""

    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
    is_superuser: bool | None = None


class UserUpdateMe(BaseModel):
    """Self-service profile update — cannot escalate privileges."""

    full_name: str | None = Field(default=None, max_length=256)
    email: EmailStr | None = None


class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserList(BaseModel):
    items: list[UserPublic]
    total: int


# ── Auth ──────────────────────────────────────────────────────────────────────


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str  # user_id as string
    exp: int | None = None
