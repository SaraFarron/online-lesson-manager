from datetime import time
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import UserSettings


class UserResponse(BaseModel):
    """Schema for User response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class AuthorizedUserResponse(BaseModel):
    """Schema for Authorized User response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    token: str
    expires_in: str
    user: UserResponse

    def form_response(self) -> dict[str, Any]:
        """Form the authorized user response dictionary."""
        return {
            "id": self.id,
            "accessToken": self.token,
            "expiresIn": self.expires_in,
            "user": UserResponse(id=self.user.id, name=self.user.name),
        }


class UserSettingsResponse(BaseModel):
    """Schema for UserSettings response."""
    morning_notification: time | None = None

    @classmethod
    def from_models(cls, user_settings: UserSettings):
        return UserSettingsResponse(morning_notification=user_settings.morning_notification)


class UserSettingsUpdate(BaseModel):
    """Schema for UserSettings update object."""
    morning_notification: time | None = None

