from pydantic import BaseModel, ConfigDict


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

    def form_response(self) -> dict:
        """Form the authorized user response dictionary."""
        return {
            "id": self.id,
            "accessToken": self.token,
            "expiresIn": self.expires_in,
            "user": UserResponse(id=self.user.id, name=self.user.name),
        }
