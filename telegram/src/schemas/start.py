from pydantic import BaseModel


class UserCreate(BaseModel):
    telegram_id: int
    full_name: str
    username: str | None
    code: str
    role: str

